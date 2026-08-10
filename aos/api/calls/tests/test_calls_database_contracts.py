from __future__ import annotations

import json
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.calls.call import (
    accept_call_impl,
    cancel_call_impl,
    end_call_impl,
    initiate_call_impl,
)
from aos.api.calls.history import list_calls_impl
from aos.api.calls.realtime import publish_call_ended
from aos.api.calls.token import get_call_token_impl
from aos.services.account_deletion_service import _end_active_calls
from aos.services.accounts.identity import public_account_id_for_user
from aos.tasks.calls import _mark_missed_and_run_side_effects
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
        # Calls reference conversations and users, so remove feature-owned rows
        # before the shared fixture cleanup deletes those parents.
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

    def _initiate(self, *, caller: str, conversation_id: str, call_type: str = "audio", notification=None):
        frappe.set_user(caller)
        notification = notification or Mock()
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.NotificationService.notify_incoming_call", notification),
            patch("aos.api.calls.call.publish_incoming_call"),
            patch("aos.api.calls.call._enqueue_call_timeout"),
            patch("aos.api.calls.call.issue_call_token", return_value="test-call-token"),
            patch("aos.api.calls.call.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            response = initiate_call_impl(conversation_id=conversation_id, call_type=call_type)
        return response, notification

    def _accept(self, *, receiver: str, call_id: str):
        frappe.set_user(receiver)
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_accepted"),
            patch("aos.api.calls.call.issue_call_token", return_value="receiver-token"),
            patch("aos.api.calls.call.get_call_ws_url", return_value="wss://calls.example.test"),
        ):
            return accept_call_impl(call_id=call_id)

    def test_initiate_retry_reuses_one_call_and_one_incoming_delivery(self):
        caller, _receiver, conversation = self._users_and_conversation()
        notification = Mock()
        first, _ = self._initiate(caller=caller, conversation_id=conversation.name, notification=notification)
        second, _ = self._initiate(caller=caller, conversation_id=conversation.name, notification=notification)

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(first["data"]["call_id"], second["data"]["call_id"])
        self.assertEqual(frappe.db.count("AOS Call", {"conversation": conversation.name}), 1)
        self.assertEqual(notification.call_count, 1)

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

    def test_accept_wins_then_cancel_cannot_regress_terminal_or_active_state(self):
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
        row = frappe.db.get_value("AOS Call", call_id, ["status", "is_active"], as_dict=True)
        self.assertEqual(row.status, "ongoing")
        self.assertEqual(int(row.is_active or 0), 1)

    def test_accepted_call_never_becomes_missed_at_timeout_boundary(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))
        with (
            patch("aos.tasks.calls.NotificationService.notify_missed_call"),
            patch("aos.tasks.calls.publish_call_not_answered"),
            patch("aos.tasks.calls.enqueue_room_cleanup"),
        ):
            changed = _mark_missed_and_run_side_effects(call_id)
        self.assertFalse(changed)
        self.assertEqual(frappe.db.get_value("AOS Call", call_id, "status"), "ongoing")

    def test_end_is_idempotent_and_marks_durable_room_cleanup(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        self.assertTrue(self._accept(receiver=receiver, call_id=call_id).get("ok"))

        frappe.set_user(caller)
        cleanup = Mock()
        with (
            patch("aos.api.calls.call.rate_limit", return_value=None),
            patch("aos.api.calls.call.publish_call_ended"),
            patch("aos.api.calls.call.enqueue_room_cleanup", cleanup),
        ):
            first = end_call_impl(call_id=call_id)
            second = end_call_impl(call_id=call_id)
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        row = frappe.db.get_value(
            "AOS Call", call_id, ["status", "is_active", "room_cleanup_pending", "duration"], as_dict=True
        )
        self.assertEqual(row.status, "ended")
        self.assertEqual(int(row.is_active or 0), 0)
        self.assertEqual(int(row.room_cleanup_pending or 0), 1)
        self.assertGreaterEqual(int(row.duration or 0), 0)
        self.assertEqual(cleanup.call_count, 1)

    def test_history_uses_public_peer_identity_and_hides_other_accounts(self):
        caller, receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
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
        call = frappe.get_doc("AOS Call", initiated["data"]["call_id"])
        call.status = "ended"
        call.is_active = 0
        call.ended_by = caller
        publish = Mock()
        with patch("aos.api.calls.realtime.frappe.publish_realtime", publish):
            publish_call_ended(call)
        self.assertEqual(publish.call_count, 2)
        for invocation in publish.call_args_list:
            self.assertTrue(invocation.kwargs.get("after_commit"))

    def test_account_deletion_cleanup_ends_active_call_and_marks_room_cleanup(self):
        caller, _receiver, conversation = self._users_and_conversation()
        initiated, _ = self._initiate(caller=caller, conversation_id=conversation.name)
        call_id = initiated["data"]["call_id"]
        cleanup = Mock()
        with patch("aos.services.calls.livekit.enqueue_room_cleanup", cleanup):
            count = _end_active_calls(user=caller, now=frappe.utils.now_datetime())
        self.assertEqual(count, 1)
        row = frappe.db.get_value(
            "AOS Call", call_id, ["status", "is_active", "room_cleanup_pending"], as_dict=True
        )
        self.assertEqual(row.status, "ended")
        self.assertEqual(int(row.is_active or 0), 0)
        self.assertEqual(int(row.room_cleanup_pending or 0), 1)
        cleanup.assert_called_once_with(call_id)


if __name__ == "__main__":
    import unittest
    unittest.main()
