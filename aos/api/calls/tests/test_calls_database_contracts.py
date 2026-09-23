from __future__ import annotations

import json
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.api.calls.call import (
    accept_call_impl,
    cancel_call_impl,
    end_call_impl,
    initiate_call_impl,
)
from aos.api.calls.history import list_calls_impl
from aos.api.calls.realtime import publish_call_ended
from aos.api.calls.status import get_call_status_impl
from aos.api.calls.token import get_call_token_impl
from aos.services.account_deletion_service import _end_active_calls
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.calls.identifiers import internal_call_name
from aos.services.livekit.admin import RoomAdminResult
from aos.tasks.calls import _mark_missed_and_run_side_effects, provision_call_room
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestCallsDatabaseContracts(AOSFeatureTestMixin, FrappeTestCase):
    """Database-backed Calls contracts for a migrated Frappe test site."""

    def setUp(self):
        self.prefix = self.make_prefix("calls-prod")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        frappe.set_user("Administrator")
        email_like = f"{self.prefix}-%@example.com"
        frappe.db.sql(
            "DELETE FROM `tabAOS Call` WHERE caller LIKE %s OR receiver LIKE %s",
            (email_like, email_like),
        )
        frappe.db.sql(
            "DELETE FROM `tabAOS Notification Delivery Job` WHERE user LIKE %s",
            (email_like,),
        )
        frappe.db.commit()
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _users_and_conversation(self, *, third: bool = False):
        caller = self.make_user("caller")
        receiver = self.make_user("receiver")
        conversation = self.make_conversation(caller, receiver)
        if not third:
            return caller, receiver, conversation
        peer = self.make_user("peer")
        second = self.make_conversation(caller, peer)
        return caller, receiver, peer, conversation, second

    def _internal(self, public_id: str) -> str:
        name = internal_call_name(public_id)
        self.assertTrue(name, public_id)
        return name

    def _mark_ready(self, public_id: str) -> str:
        name = self._internal(public_id)
        now = now_datetime()
        frappe.db.sql(
            """
            UPDATE `tabAOS Call`
            SET rtc_provisioned_at=%s,
                incoming_dispatched_at=%s,
                ring_expires_at=%s,
                state_version=state_version+1
            WHERE name=%s
            """,
            (now, now, add_to_date(now, seconds=30), name),
        )
        return name

    def _initiate(
        self,
        *,
        caller: str,
        conversation_id: str,
        call_type: str = "audio",
        ready: bool = True,
        provision_queue=None,
    ):
        frappe.set_user(caller)
        provision_queue = provision_queue or Mock()
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.enqueue_room_provisioning", provision_queue),
            patch("aos.api.calls.call.issue_call_token", return_value="test-call-token"),
            patch("aos.api.calls.call.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            response = initiate_call_impl(conversation_id=conversation_id, call_type=call_type)
        if response.get("ok") and ready:
            self._mark_ready(response["data"]["call_id"])
        return response, provision_queue

    def _accept(self, *, receiver: str, call_id: str):
        frappe.set_user(receiver)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_accepted"),
            patch("aos.api.calls.call.issue_call_token", return_value="receiver-token"),
            patch("aos.api.calls.call.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            return accept_call_impl(call_id=call_id)

    def test_initiate_retry_reuses_one_call_and_one_provisioning_job(self):
        caller, _receiver, conversation = self._users_and_conversation()
        queue = Mock()
        first, _ = self._initiate(
            caller=caller,
            conversation_id=conversation.name,
            ready=False,
            provision_queue=queue,
        )
        second, _ = self._initiate(
            caller=caller,
            conversation_id=conversation.name,
            ready=False,
            provision_queue=queue,
        )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(first["data"]["call_id"], second["data"]["call_id"])
        self.assertRegex(first["data"]["call_id"], r"^call_[0-9a-f]{32}$")
        self.assertEqual(frappe.db.count("AOS Call", {"conversation": conversation.name}), 1)
        self.assertEqual(queue.call_count, 1)
        self.assertNotIn("room_name", first["data"])
        self.assertFalse(first["data"]["rtc_ready"])

    def test_room_provisioning_dispatches_once_and_becomes_join_ready(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name, ready=False)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        notify = Mock()
        incoming = Mock()
        ready_event = Mock()

        with (
            patch("aos.tasks.calls.provision_livekit_room", return_value=RoomAdminResult(True, "created")),
            patch("aos.tasks.calls.NotificationService.notify_incoming_call", notify),
            patch("aos.tasks.calls.publish_incoming_call", incoming),
            patch("aos.tasks.calls.publish_call_ready", ready_event),
        ):
            provision_call_room(name)
            provision_call_room(name)

        row = frappe.db.get_value(
            "AOS Call",
            name,
            [
                "rtc_provisioned_at",
                "incoming_dispatched_at",
                "ring_expires_at",
                "state_version",
                "status",
            ],
            as_dict=True,
        )
        self.assertTrue(row.rtc_provisioned_at)
        self.assertTrue(row.incoming_dispatched_at)
        self.assertTrue(row.ring_expires_at)
        self.assertGreater(row.ring_expires_at, row.incoming_dispatched_at)
        self.assertEqual(row.status, "initiated")
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(incoming.call_count, 1)
        self.assertEqual(ready_event.call_count, 1)
        self.assertEqual(notify.call_args.kwargs["call_id"], call_id)

    def test_room_provision_failure_fails_closed_without_incoming_delivery(self):
        caller, _receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name, ready=False)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        notify = Mock()
        incoming = Mock()

        with (
            patch("aos.tasks.calls.provision_livekit_room", return_value=RoomAdminResult(False, "timeout")),
            patch("aos.tasks.calls.NotificationService.notify_incoming_call", notify),
            patch("aos.tasks.calls.publish_incoming_call", incoming),
            patch("aos.tasks.calls.publish_call_failed_to_caller"),
            patch("aos.tasks.calls.enqueue_room_cleanup"),
        ):
            provision_call_room(name)

        row = frappe.db.get_value(
            "AOS Call", name, ["status", "is_active", "room_cleanup_pending"], as_dict=True
        )
        self.assertEqual(row.status, "failed")
        self.assertEqual(int(row.is_active or 0), 0)
        self.assertEqual(int(row.room_cleanup_pending or 0), 1)
        notify.assert_not_called()
        incoming.assert_not_called()

    def test_participant_cannot_start_another_conversation_call(self):
        caller, _receiver, _peer, first_conversation, second_conversation = self._users_and_conversation(third=True)
        first, _ = self._initiate(caller=caller, conversation_id=first_conversation.name)
        second, _ = self._initiate(caller=caller, conversation_id=second_conversation.name)
        self.assertTrue(first.get("ok"), first)
        self.assertFalse(second.get("ok"), second)
        self.assertEqual(second.get("error"), "ACTIVE_CALL_EXISTS")

    def test_receiver_cannot_mint_token_before_accept(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        frappe.set_user(receiver)
        token_issuer = Mock(return_value="forbidden-token")
        with (
            patch("aos.api.calls.token.rate_limit", return_value=None),
            patch("aos.api.calls.token.issue_call_token", token_issuer),
        ):
            response = get_call_token_impl(call_id=call_id)
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "INVALID_STATE")
        token_issuer.assert_not_called()

    def test_block_added_while_ringing_prevents_accept_and_new_token(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        frappe.set_user("Administrator")
        frappe.get_doc(
            {
                "doctype": "AOS User Block",
                "blocker_user": receiver,
                "blocked_user": caller,
                "status": "Active",
            }
        ).insert(ignore_permissions=True)

        accepted = self._accept(receiver=receiver, call_id=call_id)
        self.assertFalse(accepted.get("ok"), accepted)
        self.assertEqual(accepted.get("error"), "USER_BLOCKED")

        frappe.set_user(receiver)
        with patch("aos.api.calls.token.rate_limit", return_value=None):
            token = get_call_token_impl(call_id=call_id)
        self.assertFalse(token.get("ok"), token)
        self.assertEqual(token.get("error"), "USER_BLOCKED")

    def test_accept_wins_then_cancel_cannot_regress_active_state(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        accepted = self._accept(receiver=receiver, call_id=call_id)
        self.assertTrue(accepted.get("ok"), accepted)

        frappe.set_user(caller)
        with patch("aos.api.calls.call.rate_limit", return_value=None):
            cancelled = cancel_call_impl(call_id=call_id)
        self.assertFalse(cancelled.get("ok"), cancelled)
        self.assertEqual(cancelled.get("error"), "INVALID_STATE")
        row = frappe.db.get_value("AOS Call", self._internal(call_id), ["status", "is_active"], as_dict=True)
        self.assertEqual(row.status, "ongoing")
        self.assertEqual(int(row.is_active or 0), 1)

    def test_accepted_call_never_becomes_missed_at_timeout_boundary(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))
        with (
            patch("aos.tasks.calls.NotificationService.notify_missed_call"),
            patch("aos.tasks.calls.publish_call_not_answered"),
            patch("aos.tasks.calls.enqueue_room_cleanup"),
        ):
            changed = _mark_missed_and_run_side_effects(name)
        self.assertFalse(changed)
        self.assertEqual(frappe.db.get_value("AOS Call", name, "status"), "ongoing")

    def test_expired_ring_cannot_be_reconstructed_accepted_or_joined_before_scheduler_runs(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        frappe.db.set_value(
            "AOS Call",
            name,
            "ring_expires_at",
            add_to_date(now_datetime(), seconds=-1),
            update_modified=False,
        )

        frappe.set_user(receiver)
        with patch("aos.api.calls.status.rate_limit", return_value=None):
            status = get_call_status_impl(call_id=call_id)
        self.assertTrue(status.get("ok"), status)
        self.assertFalse(status["data"]["can_show_incoming_ui"])
        self.assertFalse(status["data"]["can_accept"])

        accepted = self._accept(receiver=receiver, call_id=call_id)
        self.assertFalse(accepted.get("ok"), accepted)
        self.assertEqual(accepted.get("error"), "INVALID_STATE")

        frappe.set_user(caller)
        with (
            patch("aos.api.calls.token.rate_limit", return_value=None),
            patch("aos.api.calls.token.issue_call_token", return_value="stale-token") as issuer,
        ):
            token = get_call_token_impl(call_id=call_id)
        self.assertFalse(token.get("ok"), token)
        self.assertEqual(token.get("error"), "INVALID_STATE")
        issuer.assert_not_called()

    def test_end_is_idempotent_and_marks_durable_room_cleanup(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))

        frappe.set_user(caller)
        cleanup = Mock()
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_ended"),
            patch("aos.api.calls.call.enqueue_room_cleanup", cleanup),
        ):
            first = end_call_impl(call_id=call_id)
            frappe.set_user(receiver)
            second = end_call_impl(call_id=call_id)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        row = frappe.db.get_value(
            "AOS Call", name, ["status", "is_active", "room_cleanup_pending", "duration"], as_dict=True
        )
        self.assertEqual(row.status, "ended")
        self.assertEqual(int(row.is_active or 0), 0)
        self.assertEqual(int(row.room_cleanup_pending or 0), 1)
        self.assertGreaterEqual(int(row.duration or 0), 0)
        self.assertEqual(cleanup.call_count, 1)

    def test_history_uses_public_peer_identity_and_opaque_call_ids(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))
        frappe.set_user(caller)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_ended"),
            patch("aos.api.calls.call.enqueue_room_cleanup"),
        ):
            self.assertTrue(end_call_impl(call_id=call_id).get("ok"))

        with patch("aos.api.calls.history.rate_limit", return_value=None):
            history = list_calls_impl(limit=20)
        self.assertTrue(history.get("ok"), history)
        encoded = json.dumps(history.get("data"), default=str)
        self.assertNotIn(caller, encoded)
        self.assertNotIn(receiver, encoded)
        self.assertNotIn(name, encoded)
        self.assertIn(call_id, encoded)
        self.assertIn(public_account_id_for_user(receiver), encoded)

        outsider = self.make_user("outsider")
        frappe.set_user(outsider)
        with patch("aos.api.calls.history.rate_limit", return_value=None):
            outsider_history = list_calls_impl(conversation_id=conversation.name, limit=20)
        self.assertFalse(outsider_history.get("ok"), outsider_history)
        self.assertEqual(outsider_history.get("error"), "NOT_FOUND")

    def test_realtime_publish_requests_after_commit(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        call = frappe.get_doc("AOS Call", self._internal(call_id))
        call.status = "ended"
        call.is_active = 0
        call.ended_by = caller
        publish = Mock()
        with patch("aos.api.calls.realtime.frappe.publish_realtime", publish):
            publish_call_ended(call)
        self.assertEqual(publish.call_count, 2)
        for invocation in publish.call_args_list:
            self.assertTrue(invocation.kwargs.get("after_commit"))
            self.assertEqual(invocation.kwargs["message"]["call_id"], call_id)
            self.assertNotIn("room_name", invocation.kwargs["message"])


    def test_outsider_cannot_enumerate_call_or_mint_token(self):
        caller, _receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        outsider = self.make_user("outsider-token")
        frappe.set_user(outsider)
        issuer = Mock(return_value="should-not-exist")
        with (
            patch("aos.api.calls.token.rate_limit", return_value=None),
            patch("aos.api.calls.token.issue_call_token", issuer),
        ):
            response = get_call_token_impl(call_id=call_id)
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "NOT_FOUND")
        issuer.assert_not_called()

    def test_multi_session_accept_retry_is_idempotent_without_duplicate_signal(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        accepted_event = Mock()
        frappe.set_user(receiver)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_accepted", accepted_event),
            patch("aos.api.calls.call.issue_call_token", return_value="receiver-token"),
            patch("aos.api.calls.call.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            first = accept_call_impl(call_id=call_id)
            second = accept_call_impl(call_id=call_id)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(accepted_event.call_count, 1)
        self.assertEqual(
            frappe.db.get_value("AOS Call", self._internal(call_id), "status"),
            "ongoing",
        )

    def test_cancel_wins_then_late_accept_cannot_resurrect_call(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        frappe.set_user(caller)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.enqueue_room_cleanup"),
            patch("aos.api.calls.call.publish_call_cancelled"),
        ):
            cancelled = cancel_call_impl(call_id=call_id)
        self.assertTrue(cancelled.get("ok"), cancelled)

        accepted = self._accept(receiver=receiver, call_id=call_id)
        self.assertFalse(accepted.get("ok"), accepted)
        self.assertEqual(accepted.get("error"), "INVALID_STATE")
        self.assertEqual(
            frappe.db.get_value("AOS Call", self._internal(call_id), "status"),
            "cancelled",
        )

    def test_ongoing_reconnect_mints_fresh_token_and_clears_missing_room_marker(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))
        frappe.db.set_value(
            "AOS Call",
            name,
            "rtc_missing_since",
            now_datetime(),
            update_modified=False,
        )

        frappe.set_user(caller)
        with (
            patch("aos.api.calls.token.rate_limit", return_value=None),
            patch("aos.api.calls.token.issue_call_token", return_value="fresh-token"),
            patch("aos.api.calls.token.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            response = get_call_token_impl(call_id=call_id)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["token"], "fresh-token")
        self.assertIsNone(frappe.db.get_value("AOS Call", name, "rtc_missing_since"))

    def test_missed_finalization_and_notification_are_duplicate_safe(self):
        caller, _receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        name = self._internal(initiated["data"]["call_id"] )
        missed_notification = Mock()
        with (
            patch("aos.tasks.calls.NotificationService.notify_missed_call", missed_notification),
            patch("aos.tasks.calls.publish_call_not_answered"),
            patch("aos.tasks.calls.enqueue_room_cleanup"),
        ):
            expired_at = add_to_date(now_datetime(), seconds=31)
            first = _mark_missed_and_run_side_effects(name, ended_at=expired_at)
            second = _mark_missed_and_run_side_effects(name, ended_at=expired_at)
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(missed_notification.call_count, 1)

    def test_state_version_is_monotonic_across_ready_accept_and_end(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        ready_version = int(frappe.db.get_value("AOS Call", name, "state_version") or 0)
        self.assertGreaterEqual(ready_version, 2)

        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))
        accepted_version = int(frappe.db.get_value("AOS Call", name, "state_version") or 0)
        self.assertGreater(accepted_version, ready_version)

        frappe.set_user(caller)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_ended"),
            patch("aos.api.calls.call.enqueue_room_cleanup"),
        ):
            ended = end_call_impl(call_id=call_id)
        self.assertTrue(ended.get("ok"), ended)
        ended_version = int(frappe.db.get_value("AOS Call", name, "state_version") or 0)
        self.assertGreater(ended_version, accepted_version)

    def test_public_calls_indexes_exist_and_legacy_timeout_index_is_removed(self):
        for index_name in ("uq_call_public_id", "idx_call_ring_expiry", "idx_call_provision_recovery"):
            rows = frappe.db.sql(
                """
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE()
                  AND TABLE_NAME='tabAOS Call'
                  AND INDEX_NAME=%s
                LIMIT 1
                """,
                (index_name,),
            )
            self.assertTrue(rows, f"missing Calls index {index_name}")
        legacy = frappe.db.sql(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA=DATABASE()
              AND TABLE_NAME='tabAOS Call'
              AND INDEX_NAME='idx_call_timeout'
            LIMIT 1
            """
        )
        self.assertFalse(legacy, "legacy Calls timeout index still exists")

    def test_account_deletion_cleanup_ends_active_call_and_marks_room_cleanup(self):
        caller, _receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        name = self._internal(call_id)
        cleanup = Mock()
        with patch("aos.services.calls.livekit.enqueue_room_cleanup", cleanup):
            count = _end_active_calls(user=caller, now=frappe.utils.now_datetime())
        self.assertEqual(count, 1)
        row = frappe.db.get_value(
            "AOS Call", name, ["status", "is_active", "room_cleanup_pending"], as_dict=True
        )
        self.assertEqual(row.status, "ended")
        self.assertEqual(int(row.is_active or 0), 0)
        self.assertEqual(int(row.room_cleanup_pending or 0), 1)
        cleanup.assert_called_once_with(name)


if __name__ == "__main__":
    import unittest

    unittest.main()
