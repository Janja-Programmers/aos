from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.calls.call import (
    accept_call_impl,
    add_call_participants_impl,
    cancel_call_impl,
    end_call_impl,
    initiate_call_impl,
    mark_call_ringing_impl,
    reject_call_impl,
)
from aos.api.calls.history import list_calls_impl
from aos.api.calls.status import get_call_status_impl
from aos.api.calls.token import get_call_token_impl
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.calls.identifiers import internal_call_name
from aos.services.livekit.admin import RoomAdminResult
from aos.tasks.calls import _mark_participant_missed, provision_call_room
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestCallsDatabaseContracts(AOSFeatureTestMixin, FrappeTestCase):
    """Database-backed direct + conference Calls contracts."""

    def setUp(self):
        self.prefix = self.make_prefix("calls-group")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback()
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _users(self, count: int, stem: str = "user") -> list[str]:
        return [self.make_user(f"{stem}-{i}") for i in range(count)]

    @staticmethod
    def _account_ids(users: list[str]) -> list[str]:
        return [public_account_id_for_user(user) for user in users]

    def _internal(self, call_id: str) -> str:
        name = internal_call_name(call_id)
        self.assertTrue(name, call_id)
        return name

    def _mark_ready(self, call_id: str) -> str:
        name = self._internal(call_id)
        now = now_datetime()
        expires = add_to_date(now, seconds=30)
        frappe.db.sql(
            "UPDATE `tabAOS Call` SET rtc_provisioned_at=%s,state_version=state_version+1 WHERE name=%s",
            (now, name),
        )
        frappe.db.sql(
            """UPDATE `tabAOS Call Participant`
               SET incoming_dispatched_at=%s,ring_expires_at=%s
               WHERE `call`=%s AND role='participant' AND status='invited'""",
            (now, expires, name),
        )
        return name

    def _initiate(self, initiator: str, targets: list[str], *, call_type: str = "audio", ready: bool = True, conversation_id: str | None = None):
        frappe.set_user(initiator)
        queue = Mock()
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.enqueue_room_provisioning", queue),
        ):
            response = initiate_call_impl(
                participant_ids=self._account_ids(targets),
                call_type=call_type,
                conversation_id=conversation_id,
            )
        if response.get("ok") and ready:
            self._mark_ready(response["data"]["call_id"])
        return response, queue

    def _accept(self, user: str, call_id: str):
        frappe.set_user(user)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_participant_joined"),
            patch("aos.api.calls.call.issue_call_token", return_value="join-token"),
            patch("aos.api.calls.call.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            return accept_call_impl(call_id=call_id)

    def test_direct_call_uses_two_participant_ledger(self):
        initiator, target = self._users(2)
        conversation = self.make_conversation(initiator, target)
        result, queue = self._initiate(initiator, [target], conversation_id=conversation.name, ready=False)
        self.assertTrue(result.get("ok"), result)
        call_id = result["data"]["call_id"]
        name = self._internal(call_id)
        call = frappe.db.get_value("AOS Call", name, ["call_mode", "max_participants", "participant_count", "initiator"], as_dict=True)
        self.assertEqual(call.call_mode, "direct")
        self.assertEqual(int(call.max_participants), 2)
        self.assertEqual(int(call.participant_count), 2)
        self.assertEqual(call.initiator, initiator)
        rows = frappe.get_all("AOS Call Participant", filters={"call": name}, fields=["user", "role", "status"], order_by="role,user")
        self.assertEqual({row.user for row in rows}, {initiator, target})
        self.assertEqual(queue.call_count, 1)
        self.assertNotIn("room_name", result["data"])

    def test_direct_ringing_call_cancellation_is_durable_and_notifies_both_members(self):
        initiator, target = self._users(2, "direct-cancel")
        initiated, _ = self._initiate(initiator, [target])
        self.assertTrue(initiated.get("ok"), initiated)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)

        frappe.set_user(target)
        with patch("aos.api.calls.call.rate_limit", return_value=None), patch("aos.api.calls.call.publish_participant_ringing"):
            ringing = mark_call_ringing_impl(call_id=call_id)
        self.assertTrue(ringing.get("ok"), ringing)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ringing")

        frappe.set_user(initiator)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.enqueue_room_cleanup"),
            patch("aos.api.calls.call.publish_call_cancelled") as published,
        ):
            cancelled = cancel_call_impl(call_id=call_id)
        self.assertTrue(cancelled.get("ok"), cancelled)
        self.assertEqual(cancelled["data"]["status"], "cancelled")
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "cancelled")
        rows = frappe.get_all("AOS Call Participant", filters={"call": name}, fields=["user", "role", "status"])
        by_user = {row.user: row for row in rows}
        self.assertEqual(by_user[initiator].status, "left")
        self.assertEqual(by_user[target].status, "cancelled")
        published.assert_called_once()
        self.assertEqual(set(published.call_args.kwargs["users"]), {initiator, target})

    def test_group_call_supports_up_to_32_total_participants(self):
        users = self._users(32, "cap")
        initiator, targets = users[0], users[1:]
        result, _ = self._initiate(initiator, targets, ready=False)
        self.assertTrue(result.get("ok"), result)
        name = self._internal(result["data"]["call_id"])
        call = frappe.db.get_value("AOS Call", name, ["call_mode", "max_participants", "participant_count", "conversation"], as_dict=True)
        self.assertEqual(call.call_mode, "group")
        self.assertEqual(int(call.max_participants), 32)
        self.assertEqual(int(call.participant_count), 32)
        self.assertFalse(call.conversation)
        self.assertEqual(frappe.db.count("AOS Call Participant", {"call": name}), 32)

    def test_33rd_total_participant_is_rejected_before_call_creation(self):
        users = self._users(33, "over-cap")
        initiator, targets = users[0], users[1:]
        result, queue = self._initiate(initiator, targets, ready=False)
        self.assertFalse(result.get("ok"), result)
        self.assertEqual(result.get("error"), "VALIDATION_ERROR")
        self.assertEqual(queue.call_count, 0)

    def test_room_provisioning_uses_server_owned_group_capacity_and_fans_out_once(self):
        initiator, *targets = self._users(4, "provision")
        initiated, _ = self._initiate(initiator, targets, ready=False)
        name = self._internal(initiated["data"]["call_id"])
        provider = Mock(return_value=RoomAdminResult(True, "created"))
        incoming = Mock()
        notify = Mock()
        with (
            patch("aos.tasks.calls.provision_livekit_room", provider),
            patch("aos.tasks.calls.publish_incoming_call", incoming),
            patch("aos.tasks.calls.NotificationService.notify_incoming_call", notify),
            patch("aos.tasks.calls.publish_call_ready"),
        ):
            provision_call_room(name)
            provision_call_room(name)
        provider.assert_called_once()
        self.assertEqual(provider.call_args.kwargs["max_participants"], 32)
        self.assertEqual(incoming.call_count, 3)
        self.assertEqual(notify.call_count, 3)
        rows = frappe.get_all("AOS Call Participant", filters={"call": name, "role": "participant"}, fields=["incoming_dispatched_at", "ring_expires_at"])
        self.assertTrue(all(row.incoming_dispatched_at and row.ring_expires_at for row in rows))

    def test_one_group_decline_does_not_end_call_and_other_participant_can_accept(self):
        initiator, first, second = self._users(3, "decline")
        initiated, _ = self._initiate(initiator, [first, second])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        frappe.set_user(first)
        with patch("aos.api.calls.call.rate_limit", return_value=None), patch("aos.api.calls.call.publish_participant_declined"):
            declined = reject_call_impl(call_id=call_id)
        self.assertTrue(declined.get("ok"), declined)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "is_active"), 1)
        self.assertEqual(frappe.db.get_value("AOS Call Participant", {"call": name, "user": first}, "status"), "declined")
        accepted = self._accept(second, call_id)
        self.assertTrue(accepted.get("ok"), accepted)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ongoing")

    def test_group_initiator_can_leave_without_ending_active_conference(self):
        initiator, first, second = self._users(3, "leave")
        initiated, _ = self._initiate(initiator, [first, second])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(first, call_id).get("ok"))
        self.assertTrue(self._accept(second, call_id).get("ok"))
        frappe.set_user(initiator)
        with patch("aos.api.calls.call.rate_limit", return_value=None), patch("aos.api.calls.call.publish_participant_left"):
            left = end_call_impl(call_id=call_id)
        self.assertTrue(left.get("ok"), left)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ongoing")
        self.assertEqual(frappe.db.get_value("AOS Call Participant", {"call": name, "user": initiator}, "status"), "left")
        self.assertEqual(set(frappe.get_all("AOS Call Participant", filters={"call": name, "status": "joined"}, pluck="user")), {first, second})

    def test_joined_group_participant_can_invite_another_account(self):
        initiator, first, second, added = self._users(4, "add")
        initiated, _ = self._initiate(initiator, [first, second])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(first, call_id).get("ok"))
        frappe.set_user(first)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_incoming_call"),
            patch("aos.api.calls.call.NotificationService.notify_incoming_call") as notify,
        ):
            result = add_call_participants_impl(call_id=call_id, participant_ids=[public_account_id_for_user(added)])
        self.assertTrue(result.get("ok"), result)
        row = frappe.db.get_value("AOS Call Participant", {"call": name, "user": added}, ["status", "added_by", "ring_expires_at"], as_dict=True)
        self.assertEqual(row.status, "invited")
        self.assertEqual(row.added_by, first)
        self.assertTrue(row.ring_expires_at)
        self.assertEqual(int(frappe.db.get_value("AOS Call", name, "participant_count")), 4)
        self.assertEqual(notify.call_args.kwargs["caller"], first)

    def test_add_participants_retry_is_idempotent_and_does_not_duplicate_delivery(self):
        initiator, first, second, added = self._users(4, "add-retry")
        initiated, _ = self._initiate(initiator, [first, second])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(first, call_id).get("ok"))
        frappe.set_user(first)
        account_id = public_account_id_for_user(added)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_participants_invited") as invited_event,
            patch("aos.api.calls.call.publish_incoming_call") as incoming,
            patch("aos.api.calls.call.NotificationService.notify_incoming_call") as notify,
        ):
            first_result = add_call_participants_impl(call_id=call_id, participant_ids=[account_id])
            retry_result = add_call_participants_impl(call_id=call_id, participant_ids=[account_id])
        self.assertTrue(first_result.get("ok"), first_result)
        self.assertTrue(retry_result.get("ok"), retry_result)
        self.assertEqual(frappe.db.count("AOS Call Participant", {"call": name, "user": added}), 1)
        self.assertEqual(int(frappe.db.get_value("AOS Call", name, "participant_count")), 4)
        self.assertEqual(invited_event.call_count, 1)
        self.assertEqual(incoming.call_count, 1)
        self.assertEqual(notify.call_count, 1)

    def test_last_joined_group_participant_ends_call_and_cancels_pending_invites(self):
        initiator, joined, pending = self._users(3, "last-leave")
        initiated, _ = self._initiate(initiator, [joined, pending])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(joined, call_id).get("ok"))

        frappe.set_user(initiator)
        with patch("aos.api.calls.call.rate_limit", return_value=None), patch("aos.api.calls.call.publish_participant_left"):
            initiator_left = end_call_impl(call_id=call_id)
        self.assertTrue(initiator_left.get("ok"), initiator_left)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ongoing")

        frappe.set_user(joined)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_ended"),
            patch("aos.api.calls.call.enqueue_room_cleanup"),
        ):
            ended = end_call_impl(call_id=call_id)
        self.assertTrue(ended.get("ok"), ended)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ended")
        self.assertEqual(
            frappe.db.get_value("AOS Call Participant", {"call": name, "user": pending}, "status"),
            "cancelled",
        )
        self.assertFalse(frappe.db.exists("AOS Call Participant", {"call": name, "status": "joined"}))

    def test_ongoing_direct_call_promotes_to_group_without_room_recreation(self):
        initiator, peer, added = self._users(3, "promote")
        conversation = self.make_conversation(initiator, peer)
        initiated, _ = self._initiate(initiator, [peer], conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(peer, call_id).get("ok"))
        frappe.db.sql(
            "UPDATE `tabAOS Call` SET video_upgrade_status='requested',video_upgrade_requested_by=%s WHERE name=%s",
            (initiator, name),
        )
        frappe.set_user(peer)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_participants_invited") as invited_event,
            patch("aos.api.calls.call.publish_incoming_call"),
            patch("aos.api.calls.call.NotificationService.notify_incoming_call"),
        ):
            result = add_call_participants_impl(
                call_id=call_id, participant_ids=[public_account_id_for_user(added)]
            )
        self.assertTrue(result.get("ok"), result)
        call = frappe.db.get_value(
            "AOS Call", name,
            ["call_mode", "max_participants", "participant_count", "conversation", "video_upgrade_status"],
            as_dict=True,
        )
        self.assertEqual(call.call_mode, "group")
        self.assertEqual(int(call.max_participants), 32)
        self.assertEqual(int(call.participant_count), 3)
        self.assertFalse(call.conversation)
        self.assertEqual(call.video_upgrade_status, "none")
        invited_event.assert_called_once()

    def test_direct_decline_publishes_terminal_state_to_caller(self):
        initiator, target = self._users(2, "direct-decline-event")
        initiated, _ = self._initiate(initiator, [target])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        frappe.set_user(target)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_participant_declined") as participant_event,
            patch("aos.api.calls.call.publish_call_ended") as terminal_event,
            patch("aos.api.calls.call.enqueue_room_cleanup"),
        ):
            result = reject_call_impl(call_id=call_id)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "rejected")
        self.assertEqual(int(frappe.db.get_value("AOS Call", name, "is_active") or 0), 0)
        participant_event.assert_called_once()
        terminal_event.assert_called_once()
        self.assertEqual(terminal_event.call_args.kwargs.get("event_status"), "rejected")

    def test_direct_end_terminates_both_participants(self):
        initiator, target = self._users(2, "direct-end")
        initiated, _ = self._initiate(initiator, [target])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(target, call_id).get("ok"))
        frappe.set_user(initiator)
        with patch("aos.api.calls.call.rate_limit", return_value=None), patch("aos.api.calls.call.publish_call_ended"), patch("aos.api.calls.call.enqueue_room_cleanup"):
            result = end_call_impl(call_id=call_id)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ended")
        self.assertFalse(frappe.db.exists("AOS Call Participant", {"call": name, "status": "joined"}))

    def test_direct_receiver_can_end_after_accept(self):
        initiator, target = self._users(2, "direct-receiver-end")
        initiated, _ = self._initiate(initiator, [target])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(target, call_id).get("ok"))
        # _accept leaves the request user as the receiver. Ending from this side
        # must terminalize the same direct Call, not only disconnect local RTC.
        with patch("aos.api.calls.call.rate_limit", return_value=None), patch("aos.api.calls.call.publish_call_ended"), patch("aos.api.calls.call.enqueue_room_cleanup"):
            result = end_call_impl(call_id=call_id)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ended")
        self.assertEqual(int(frappe.db.get_value("AOS Call", name, "is_active") or 0), 0)
        self.assertFalse(frappe.db.exists("AOS Call Participant", {"call": name, "status": "joined"}))

    def test_invited_participant_cannot_mint_token_until_joined(self):
        initiator, target = self._users(2, "token")
        initiated, _ = self._initiate(initiator, [target])
        call_id = initiated["data"]["call_id"]
        frappe.set_user(target)
        issuer = Mock(return_value="bad")
        with patch("aos.api.calls.token.rate_limit", return_value=None), patch("aos.api.calls.token.issue_call_token", issuer):
            denied = get_call_token_impl(call_id=call_id)
        self.assertFalse(denied.get("ok"), denied)
        self.assertEqual(denied.get("error"), "INVALID_STATE")
        issuer.assert_not_called()
        self.assertTrue(self._accept(target, call_id).get("ok"))
        with patch("aos.api.calls.token.rate_limit", return_value=None), patch("aos.api.calls.token.issue_call_token", return_value="fresh"), patch("aos.api.calls.token.get_call_ws_url", return_value="wss://calls.example.test"):
            allowed = get_call_token_impl(call_id=call_id)
        self.assertTrue(allowed.get("ok"), allowed)
        self.assertEqual(allowed["data"]["token"], "fresh")

    def test_one_missed_group_invitee_does_not_end_ongoing_call(self):
        initiator, joined, missed = self._users(3, "missed")
        initiated, _ = self._initiate(initiator, [joined, missed])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(joined, call_id).get("ok"))
        row = frappe.db.get_value("AOS Call Participant", {"call": name, "user": missed}, ["name", "call", "user", "added_by"], as_dict=True)
        now = add_to_date(now_datetime(), seconds=31)
        frappe.db.set_value("AOS Call Participant", row.name, "ring_expires_at", add_to_date(now, seconds=-1), update_modified=False)
        with patch("aos.tasks.calls.publish_participant_missed"), patch("aos.tasks.calls.NotificationService.notify_missed_call"):
            changed = _mark_participant_missed(SimpleNamespace(**row), now)
        self.assertTrue(changed)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ongoing")
        self.assertEqual(frappe.db.get_value("AOS Call Participant", row.name, "status"), "missed")

    def test_history_uses_opaque_ids_and_participant_membership(self):
        initiator, target = self._users(2, "history")
        initiated, _ = self._initiate(initiator, [target])
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        frappe.set_user(target)
        with patch("aos.api.calls.history.rate_limit", return_value=None):
            result = list_calls_impl(limit=20)
        self.assertTrue(result.get("ok"), result)
        encoded = str(result["data"])
        self.assertIn(call_id, encoded)
        self.assertNotIn(name, encoded)
        self.assertNotIn(target, encoded)
        self.assertNotIn(initiator, encoded)

    def test_schema_indexes_and_legacy_columns_are_removed(self):
        required = {
            ("tabAOS Call", "uq_call_public_id"),
            ("tabAOS Call", "idx_call_provision_recovery"),
            ("tabAOS Call Participant", "uq_call_participant"),
            ("tabAOS Call Participant", "idx_call_participant_expiry"),
        }
        for table, index in required:
            rows = frappe.db.sql("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1", (table, index))
            self.assertTrue(rows, f"missing {index}")
        for field in ("naming_series", "caller", "receiver", "incoming_dispatched_at", "ring_expires_at", "visible_to_caller", "visible_to_receiver"):
            self.assertFalse(frappe.db.has_column("AOS Call", field), field)
        self.assertFalse(frappe.db.sql("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='tabAOS Call' AND INDEX_NAME='idx_call_timeout' LIMIT 1"))

    def test_public_status_exposes_participant_state_without_internal_identity(self):
        initiator, target = self._users(2, "status")
        initiated, _ = self._initiate(initiator, [target])
        frappe.set_user(target)
        with patch("aos.api.calls.status.rate_limit", return_value=None):
            result = get_call_status_impl(call_id=initiated["data"]["call_id"])
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["data"]["participant_status"], "invited")
        self.assertNotIn("room_name", result["data"])
        self.assertNotIn(target, str(result["data"]))


if __name__ == "__main__":
    import unittest
    unittest.main()
