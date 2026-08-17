from __future__ import annotations

from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.chat.conversation import delete_conversation_impl, list_conversations_impl
from aos.api.chat.message import list_messages_impl, send_message_impl
from aos.api.chat.status import mark_delivered_impl, mark_read_impl
from aos.api.chat.presence import get_presence_impl
from aos.api.live.share import share_live_to_chat_impl
from aos.services.live.api import run_live_api
from aos.services.live.endpoints import ENDPOINT_SPECS as LIVE_ENDPOINT_SPECS
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestChatDatabaseContracts(AOSFeatureTestMixin, FrappeTestCase):
    """Database-backed Chat hardening tests for a migrated Frappe site."""

    def setUp(self):
        self.prefix = self.make_prefix("chat-prod")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _users_and_conversation(self):
        sender = self.make_user("sender")
        receiver = self.make_user("receiver")
        conversation = self.make_conversation(sender, receiver)
        return sender, receiver, conversation

    def _share_live_to_chat(self, **kwargs):
        return run_live_api(
            share_live_to_chat_impl,
            kwargs,
            spec=LIVE_ENDPOINT_SPECS["share_live_to_chat"],
            operation_name="share_live_to_chat",
            transactional=True,
        )


    def test_presence_snapshot_returns_peer_public_identity_and_last_seen(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.db.set_value("User", receiver, "last_active", frappe.utils.now_datetime(), update_modified=False)
        frappe.set_user(sender)
        with patch("aos.api.chat.presence.rate_limit", return_value=None):
            response = get_presence_impl(conversation_id=conversation.name)

        self.assertTrue(response.get("ok"), response)
        data = response.get("data") or {}
        self.assertTrue(str(data.get("user") or "").startswith("ACC-"))
        self.assertNotIn("@", str(data.get("user") or ""))
        self.assertTrue(data.get("display_name"))
        self.assertTrue(data.get("last_seen"))
        self.assertTrue(data.get("is_online"))

    def test_conversation_list_exposes_last_outgoing_receipt_state(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            sent = send_message_impl(
                conversation_id=conversation.name,
                content="Receipt preview",
                idempotency_key="receipt-preview-1",
            )
        self.assertTrue(sent.get("ok"), sent)

        with patch("aos.api.chat.conversation.rate_limit", return_value=None):
            initial = list_conversations_impl(limit=20, offset=0)
        row = next(item for item in (initial.get("data") or []) if item.get("id") == conversation.name)
        self.assertTrue(row.get("last_message_id"))
        self.assertTrue(row.get("last_message_is_mine"))
        self.assertIsNone(row.get("last_message_delivered_at"))
        self.assertIsNone(row.get("last_message_read_at"))

        frappe.set_user(receiver)
        with patch("aos.api.chat.status.rate_limit", return_value=None):
            delivered = mark_delivered_impl(conversation_id=conversation.name)
        self.assertTrue(delivered.get("ok"), delivered)

        frappe.set_user(sender)
        with patch("aos.api.chat.conversation.rate_limit", return_value=None):
            after_delivery = list_conversations_impl(limit=20, offset=0)
        row = next(item for item in (after_delivery.get("data") or []) if item.get("id") == conversation.name)
        self.assertTrue(row.get("last_message_delivered_at"))
        self.assertIsNone(row.get("last_message_read_at"))

        frappe.set_user(receiver)
        with patch("aos.api.chat.status.rate_limit", return_value=None):
            read = mark_read_impl(conversation_id=conversation.name)
        self.assertTrue(read.get("ok"), read)

        frappe.set_user(sender)
        with patch("aos.api.chat.conversation.rate_limit", return_value=None):
            after_read = list_conversations_impl(limit=20, offset=0)
        row = next(item for item in (after_read.get("data") or []) if item.get("id") == conversation.name)
        self.assertTrue(row.get("last_message_delivered_at"))
        self.assertTrue(row.get("last_message_read_at"))


    def test_delete_conversation_also_clears_old_history_for_that_user(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            first = send_message_impl(
                conversation_id=conversation.name,
                content="Old message one",
                idempotency_key="delete-clear-1",
            )
            second = send_message_impl(
                conversation_id=conversation.name,
                content="Old message two",
                idempotency_key="delete-clear-2",
            )
        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)

        frappe.set_user(receiver)
        with patch("aos.api.chat.conversation.rate_limit", return_value=None):
            deleted = delete_conversation_impl(conversation_id=conversation.name)
        self.assertTrue(deleted.get("ok"), deleted)
        self.assertEqual(deleted.get("data", {}).get("cleared_count"), 2)

        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            new_message = send_message_impl(
                conversation_id=conversation.name,
                content="New message after delete",
                idempotency_key="delete-clear-3",
            )
        self.assertTrue(new_message.get("ok"), new_message)

        frappe.set_user(receiver)
        with patch("aos.api.chat.message.rate_limit", return_value=None):
            history = list_messages_impl(conversation_id=conversation.name, limit=20)
        self.assertTrue(history.get("ok"), history)
        ids = [row.get("id") for row in (history.get("data") or [])]
        self.assertEqual(ids, [new_message.get("data", {}).get("id")])

    def test_message_idempotency_prevents_duplicate_side_effects(self):
        sender, _receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        notification = Mock()
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message", notification),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            first = send_message_impl(
                conversation_id=conversation.name,
                content="Idempotent hello",
                idempotency_key="device-op-1",
            )
            second = send_message_impl(
                conversation_id=conversation.name,
                content="Idempotent hello",
                idempotency_key="device-op-1",
            )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(first.get("data", {}).get("id"), second.get("data", {}).get("id"))
        self.assertEqual(
            frappe.db.count("AOS Message", {"conversation": conversation.name, "sender": sender}),
            1,
        )
        notification.assert_called_once()

    def test_native_live_share_persists_live_reference_not_url(self):
        sender, _receiver, conversation = self._users_and_conversation()
        live = self.make_live(host=sender)
        frappe.set_user(sender)
        with (
            patch("aos.api.live.share.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            response = self._share_live_to_chat(
                live_id=live.name,
                conversation_id=conversation.name,
                message="Come watch",
                idempotency_key="live-share-1",
            )

        self.assertTrue(response.get("ok"), response)
        message = response.get("data", {}).get("message", {})
        self.assertEqual(message.get("live"), live.name)
        self.assertEqual(message.get("message_type"), "mixed")
        self.assertEqual(message.get("content"), "Come watch")
        self.assertEqual(message.get("live_preview", {}).get("live_id"), live.name)
        stored = frappe.db.get_value(
            "AOS Message",
            message.get("id"),
            ["live", "message_type", "content"],
            as_dict=True,
        )
        self.assertEqual(stored.live, live.name)
        self.assertEqual(stored.message_type, "mixed")
        self.assertNotIn("/live/", str(stored.content or ""))

    def test_live_share_rejects_ended_live(self):
        sender, _receiver, conversation = self._users_and_conversation()
        live = self.make_live(host=sender)
        frappe.db.set_value(
            "AOS Live Stream",
            live.name,
            {"status": "ended", "is_active": 0},
            update_modified=False,
        )
        frappe.set_user(sender)
        with patch("aos.api.live.share.rate_limit", return_value=None):
            response = self._share_live_to_chat(
                live_id=live.name,
                conversation_id=conversation.name,
            )

        self.assertFalse(response.get("ok"), response)
        # An ended Live is intentionally indistinguishable from a missing or
        # otherwise inaccessible Live at this feature-owned public boundary.
        # This prevents callers from using share-to-chat to enumerate private
        # lifecycle state for canonical LIVE-* identifiers.
        self.assertEqual(response.get("error"), "LIVE_CHAT_TARGET_NOT_FOUND")
        self.assertEqual(frappe.db.count("AOS Message", {"live": live.name}), 0)

    def test_generic_chat_send_rejects_new_ended_live_reference(self):
        sender, _receiver, conversation = self._users_and_conversation()
        live = self.make_live(host=sender)
        frappe.db.set_value(
            "AOS Live Stream",
            live.name,
            {"status": "ended", "is_active": 0},
            update_modified=False,
        )
        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
        ):
            response = send_message_impl(
                conversation_id=conversation.name,
                live=live.name,
                idempotency_key="ended-live-direct-1",
            )

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(frappe.db.count("AOS Message", {"live": live.name}), 0)

    def test_live_history_returns_ended_preview_but_not_session_credentials(self):
        sender, receiver, conversation = self._users_and_conversation()
        live = self.make_live(host=sender)
        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            sent = send_message_impl(
                conversation_id=conversation.name,
                live=live.name,
                content="Watch",
                idempotency_key="history-live-1",
            )
        self.assertTrue(sent.get("ok"), sent)
        frappe.db.set_value(
            "AOS Live Stream",
            live.name,
            {"status": "ended", "is_active": 0},
            update_modified=False,
        )
        frappe.set_user(receiver)
        with patch("aos.api.chat.message.rate_limit", return_value=None):
            history = list_messages_impl(conversation_id=conversation.name, limit=20)
        self.assertTrue(history.get("ok"), history)
        item = next(row for row in (history.get("data") or []) if row.get("live") == live.name)
        self.assertEqual(item.get("live_preview", {}).get("status"), "ended")
        serialized = repr(item).lower()
        self.assertNotIn("token", serialized)
        self.assertNotIn("session_id", serialized)
        self.assertNotIn("room_name", serialized)

    def test_unavailable_ad_cannot_be_shared_or_leaked_in_history(self):
        sender, receiver, conversation = self._users_and_conversation()
        ad = self.make_ad(seller_user=sender, status="Active")
        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            sent = send_message_impl(
                conversation_id=conversation.name,
                ad=ad.name,
                content="Look at this",
                idempotency_key="ad-history-1",
            )
        self.assertTrue(sent.get("ok"), sent)
        self.assertEqual(sent.get("data", {}).get("ad_preview", {}).get("id"), ad.name)

        frappe.db.set_value("AOS Ad", ad.name, "status", "Suspended", update_modified=False)
        frappe.set_user(receiver)
        with patch("aos.api.chat.message.rate_limit", return_value=None):
            history = list_messages_impl(conversation_id=conversation.name, limit=20)
        self.assertTrue(history.get("ok"), history)
        item = next(row for row in (history.get("data") or []) if row.get("ad") == ad.name)
        self.assertIsNone(item.get("ad_preview"))
        self.assertTrue(item.get("ad_unavailable"))

        frappe.set_user(sender)
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message"),
        ):
            rejected = send_message_impl(
                conversation_id=conversation.name,
                ad=ad.name,
                idempotency_key="ad-history-2",
            )
        self.assertFalse(rejected.get("ok"), rejected)
        self.assertEqual(rejected.get("error"), "NOT_FOUND")

    def test_chat_notification_receives_message_id_for_dedupe(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        notification = Mock()
        with (
            patch("aos.api.chat.message.rate_limit", return_value=None),
            patch("aos.api.chat.message.NotificationService.notify_new_message", notification),
            patch("aos.api.chat.message.enqueue_conversation_response_metrics_refresh"),
        ):
            response = send_message_impl(conversation_id=conversation.name, content="Notify")
        self.assertTrue(response.get("ok"), response)
        kwargs = notification.call_args.kwargs
        self.assertEqual(kwargs["user"], receiver)
        self.assertEqual(kwargs["conversation_id"], conversation.name)
        self.assertEqual(kwargs["message_id"], response.get("data", {}).get("id"))


if __name__ == "__main__":
    import unittest

    unittest.main()
