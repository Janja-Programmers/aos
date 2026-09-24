from __future__ import annotations

from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.live.share import share_live_to_chat_impl
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.chat.api import run_chat_api
from aos.services.chat.conversation_ops import (
    add_group_members_impl,
    create_group_impl,
    delete_conversation_impl,
    list_conversations_impl,
    list_locked_conversations_impl,
    remove_group_member_impl,
    set_group_member_role_impl,
)
from aos.services.chat.endpoints import ENDPOINT_SPECS, TRANSACTIONAL_ENDPOINTS
from aos.services.chat.lock_ops import (
    configure_chat_lock_secret_impl,
    set_conversation_lock_impl,
    verify_chat_lock_secret_impl,
)
from aos.services.chat.message_ops import list_messages_impl, send_message_impl
from aos.services.chat.presence_ops import get_presence_impl
from aos.services.chat.status_ops import mark_read_impl
from aos.services.chat.translation_ops import translate_message_impl
from aos.services.live.api import run_live_api
from aos.services.live.endpoints import ENDPOINT_SPECS as LIVE_ENDPOINT_SPECS
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class _MemoryCache:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set_value(self, key, value, expires_in_sec=None):
        self.values[str(key)] = str(value)
        return True

    def get_value(self, key):
        return self.values.get(str(key))

    def delete_value(self, key):
        self.values.pop(str(key), None)


class TestChatDatabaseContracts(AOSFeatureTestMixin, FrappeTestCase):
    """Database-backed contracts for normalized Chat, groups, lock, and translation."""

    def setUp(self):
        self.prefix = self.make_prefix("chat-prod")
        self.created_users: list[str] = []
        self.memory_cache = _MemoryCache()
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

        self._patchers = [
            patch("aos.services.chat.conversation_ops.rate_limit", return_value=None),
            patch("aos.services.chat.message_ops.rate_limit", return_value=None),
            patch("aos.services.chat.status_ops.rate_limit", return_value=None),
            patch("aos.services.chat.presence_ops.rate_limit", return_value=None),
            patch("aos.services.chat.lock_ops.rate_limit", return_value=None),
            patch("aos.services.chat.message_mutations.rate_limit", return_value=None),
            patch("aos.services.chat.translation_ops.rate_limit", return_value=None),
            patch("aos.services.chat.presence_ops.schedule_presence_update_to_peers", return_value=None),
            patch("aos.services.chat.message_ops.enqueue_conversation_response_metrics_refresh", return_value=None),
            patch("aos.services.chat.lock._cache", return_value=self.memory_cache),
        ]
        for patcher in self._patchers:
            patcher.start()
        self.notification = patch(
            "aos.services.chat.message_ops.NotificationService.notify_new_message"
        ).start()

    def tearDown(self):
        try:
            self.cleanup_feature_rows()
        finally:
            patch.stopall()
            frappe.set_user("Administrator")

    def _chat(self, name: str, implementation, **kwargs):
        return run_chat_api(
            implementation,
            kwargs,
            spec=ENDPOINT_SPECS[name],
            operation_name=name,
            transactional=name in TRANSACTIONAL_ENDPOINTS,
        )

    def _users_and_conversation(self):
        sender = self.make_user("sender")
        receiver = self.make_user("receiver")
        conversation = self.make_conversation(sender, receiver)
        return sender, receiver, conversation

    def _create_group(self, owner: str, *members: str, title: str = "Production Group") -> str:
        frappe.set_user(owner)
        result = self._chat(
            "create_group",
            create_group_impl,
            title=title,
            participant_ids=[public_account_id_for_user(user) for user in members],
        )
        self.assertTrue(result.get("ok"), result)
        return str(result["data"]["id"])

    def _share_live_to_chat(self, **kwargs):
        return run_live_api(
            share_live_to_chat_impl,
            kwargs,
            spec=LIVE_ENDPOINT_SPECS["share_live_to_chat"],
            operation_name="share_live_to_chat",
            transactional=True,
        )

    def test_direct_presence_uses_public_identity_only(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.db.set_value("User", receiver, "last_active", frappe.utils.now_datetime(), update_modified=False)
        frappe.set_user(sender)
        response = self._chat("get_presence", get_presence_impl, conversation_id=conversation.name)

        self.assertTrue(response.get("ok"), response)
        participants = response.get("data", {}).get("participants") or []
        self.assertEqual(len(participants), 1)
        peer = participants[0]
        self.assertTrue(str(peer.get("account_id") or "").startswith("ACC-"))
        self.assertNotIn("@", str(peer.get("account_id") or ""))
        self.assertTrue(peer.get("display_name"))
        self.assertTrue(peer.get("last_seen"))

    def test_message_idempotency_replays_exact_request_and_conflicts_on_reuse(self):
        sender, _receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        first = self._chat(
            "send_message", send_message_impl,
            conversation_id=conversation.name, content="Idempotent hello", idempotency_key="device-op-1",
        )
        replay = self._chat(
            "send_message", send_message_impl,
            conversation_id=conversation.name, content="Idempotent hello", idempotency_key="device-op-1",
        )
        conflict = self._chat(
            "send_message", send_message_impl,
            conversation_id=conversation.name, content="Different payload", idempotency_key="device-op-1",
        )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(replay.get("ok"), replay)
        self.assertEqual(first["data"]["id"], replay["data"]["id"])
        self.assertFalse(conflict.get("ok"), conflict)
        self.assertEqual(conflict.get("error"), "CHAT_CONFLICT")
        self.assertEqual(frappe.db.count("AOS Message", {"conversation": conversation.name}), 1)
        self.assertEqual(self.notification.call_count, 1)

    def test_delete_conversation_advances_private_history_watermark(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        old_one = self._chat("send_message", send_message_impl, conversation_id=conversation.name, content="Old one")
        old_two = self._chat("send_message", send_message_impl, conversation_id=conversation.name, content="Old two")
        self.assertTrue(old_one.get("ok") and old_two.get("ok"))

        frappe.set_user(receiver)
        deleted = self._chat("delete_conversation", delete_conversation_impl, conversation_id=conversation.name)
        self.assertTrue(deleted.get("ok"), deleted)
        self.assertTrue(deleted.get("data", {}).get("cleared_before"))

        frappe.set_user(sender)
        fresh = self._chat("send_message", send_message_impl, conversation_id=conversation.name, content="Fresh")
        self.assertTrue(fresh.get("ok"), fresh)

        frappe.set_user(receiver)
        history = self._chat("list_messages", list_messages_impl, conversation_id=conversation.name, limit=20)
        self.assertTrue(history.get("ok"), history)
        self.assertEqual([row["id"] for row in history["data"]["items"]], [fresh["data"]["id"]])

    def test_group_membership_roles_history_boundary_and_idor(self):
        owner = self.make_user("owner")
        member = self.make_user("member")
        late = self.make_user("late")
        outsider = self.make_user("outsider")
        group_id = self._create_group(owner, member)

        frappe.set_user(owner)
        before = self._chat("send_message", send_message_impl, conversation_id=group_id, content="Before join")
        added = self._chat(
            "add_group_members", add_group_members_impl,
            conversation_id=group_id, participant_ids=[public_account_id_for_user(late)],
        )
        after = self._chat("send_message", send_message_impl, conversation_id=group_id, content="After join")
        promoted = self._chat(
            "set_group_member_role", set_group_member_role_impl,
            conversation_id=group_id, account_id=public_account_id_for_user(member), role="admin",
        )
        self.assertTrue(before.get("ok") and added.get("ok") and after.get("ok") and promoted.get("ok"))

        late_row = frappe.db.get_value(
            "AOS Conversation Participant", {"conversation": group_id, "user": late}, ["status", "role"], as_dict=True
        )
        self.assertEqual(late_row.status, "active")
        member_role = frappe.db.get_value(
            "AOS Conversation Participant", {"conversation": group_id, "user": member}, "role"
        )
        self.assertEqual(member_role, "admin")

        frappe.set_user(late)
        history = self._chat("list_messages", list_messages_impl, conversation_id=group_id, limit=20)
        self.assertTrue(history.get("ok"), history)
        self.assertEqual([item["id"] for item in history["data"]["items"]], [after["data"]["id"]])

        frappe.set_user(outsider)
        denied = self._chat("list_messages", list_messages_impl, conversation_id=group_id, limit=20)
        self.assertFalse(denied.get("ok"), denied)
        self.assertEqual(denied.get("error"), "CHAT_NOT_FOUND")

        frappe.set_user(owner)
        removed = self._chat(
            "remove_group_member", remove_group_member_impl,
            conversation_id=group_id, account_id=public_account_id_for_user(late),
        )
        self.assertTrue(removed.get("ok"), removed)
        frappe.set_user(late)
        denied_after_remove = self._chat("list_messages", list_messages_impl, conversation_id=group_id, limit=20)
        self.assertEqual(denied_after_remove.get("error"), "CHAT_NOT_FOUND")

    def test_group_read_state_is_per_member_and_unread_reconciles(self):
        owner = self.make_user("owner")
        member = self.make_user("member")
        group_id = self._create_group(owner, member)
        frappe.set_user(owner)
        one = self._chat("send_message", send_message_impl, conversation_id=group_id, content="One")
        two = self._chat("send_message", send_message_impl, conversation_id=group_id, content="Two")
        self.assertTrue(one.get("ok") and two.get("ok"))

        unread = frappe.db.get_value(
            "AOS Conversation Participant", {"conversation": group_id, "user": member}, "unread_count"
        )
        self.assertEqual(int(unread or 0), 2)
        frappe.set_user(member)
        read = self._chat("mark_read", mark_read_impl, conversation_id=group_id)
        self.assertTrue(read.get("ok"), read)
        self.assertEqual(read["data"]["updated_count"], 2)
        self.assertFalse(read["data"]["has_more"])
        unread_after = frappe.db.get_value(
            "AOS Conversation Participant", {"conversation": group_id, "user": member}, "unread_count"
        )
        self.assertEqual(int(unread_after or 0), 0)
        states = frappe.db.sql(
            """SELECT COUNT(*) FROM `tabAOS Message User State`
               WHERE conversation=%s AND user=%s AND read_at IS NOT NULL""",
            (group_id, member),
        )[0][0]
        self.assertEqual(int(states), 2)

    def test_hidden_chat_lock_requires_secret_token_even_to_unlock(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(receiver)
        locked = self._chat("set_conversation_lock", set_conversation_lock_impl, conversation_id=conversation.name, locked=1)
        configured = self._chat(
            "configure_chat_lock_secret", configure_chat_lock_secret_impl,
            secret="Blue Mango 47", hide_locked_chats=1,
        )
        self.assertTrue(locked.get("ok") and configured.get("ok"))

        normal = self._chat("list_conversations", list_conversations_impl, limit=20)
        hidden_folder = self._chat("list_locked_conversations", list_locked_conversations_impl, limit=20)
        hidden_history = self._chat("list_messages", list_messages_impl, conversation_id=conversation.name, limit=20)
        bypass = self._chat(
            "set_conversation_lock", set_conversation_lock_impl,
            conversation_id=conversation.name, locked=0,
        )
        self.assertEqual(normal["data"]["items"], [])
        self.assertEqual(hidden_folder["data"]["items"], [])
        self.assertTrue(hidden_folder["data"]["hidden"])
        self.assertEqual(hidden_history.get("error"), "CHAT_NOT_FOUND")
        self.assertEqual(bypass.get("error"), "CHAT_NOT_FOUND")

        verified = self._chat("verify_chat_lock_secret", verify_chat_lock_secret_impl, secret="Blue Mango 47")
        self.assertTrue(verified.get("ok"), verified)
        token = verified["data"]["lock_token"]
        locked_list = self._chat("list_locked_conversations", list_locked_conversations_impl, limit=20, lock_token=token)
        self.assertEqual([item["id"] for item in locked_list["data"]["items"]], [conversation.name])
        unlocked = self._chat(
            "set_conversation_lock", set_conversation_lock_impl,
            conversation_id=conversation.name, locked=0, lock_token=token,
        )
        self.assertTrue(unlocked.get("ok"), unlocked)

    def test_locked_recipient_notification_requests_private_preview(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(receiver)
        locked = self._chat("set_conversation_lock", set_conversation_lock_impl, conversation_id=conversation.name, locked=1)
        self.assertTrue(locked.get("ok"), locked)

        frappe.set_user(sender)
        sent = self._chat("send_message", send_message_impl, conversation_id=conversation.name, content="Sensitive preview")
        self.assertTrue(sent.get("ok"), sent)
        kwargs = self.notification.call_args.kwargs
        self.assertEqual(kwargs["user"], receiver)
        self.assertEqual(kwargs["conversation_id"], conversation.name)
        self.assertEqual(kwargs["message_id"], sent["data"]["id"])
        self.assertTrue(kwargs["private_preview"])

    def test_chat_translation_to_german_is_cached_by_requested_languages(self):
        sender, receiver, conversation = self._users_and_conversation()
        frappe.set_user(sender)
        sent = self._chat("send_message", send_message_impl, conversation_id=conversation.name, content="Hello world")
        self.assertTrue(sent.get("ok"), sent)

        provider = Mock(return_value={
            "source_language": "eng_Latn",
            "source_language_label": "English",
            "target_language": "deu_Latn",
            "target_language_label": "German",
            "translated_content": "Hallo Welt",
            "provider": "nllb",
            "model_name": "test-nllb",
        })
        frappe.set_user(receiver)
        with patch("aos.services.chat.translation_ops.translate_text", provider):
            first = self._chat(
                "translate_message", translate_message_impl,
                message_id=sent["data"]["id"], target_language="de",
            )
            cached = self._chat(
                "translate_message", translate_message_impl,
                message_id=sent["data"]["id"], target_language="de",
            )
        self.assertTrue(first.get("ok") and cached.get("ok"))
        self.assertEqual(first["data"]["target_language"], "deu_Latn")
        self.assertEqual(first["data"]["target_language_label"], "German")
        self.assertEqual(first["data"]["translated_content"], "Hallo Welt")
        self.assertFalse(first["data"]["cached"])
        self.assertTrue(cached["data"]["cached"])
        self.assertNotIn("translated_by", first["data"])
        provider.assert_called_once()
        stored = frappe.db.get_value(
            "AOS Message Translation",
            {"message": sent["data"]["id"]},
            ["request_source_language", "request_target_language"],
            as_dict=True,
        )
        self.assertEqual(stored.request_source_language, "__default__")
        self.assertEqual(stored.request_target_language, "de")

    def test_live_share_stores_canonical_reference_and_ended_live_is_not_newly_shareable(self):
        sender, _receiver, conversation = self._users_and_conversation()
        live = self.make_live(host=sender)
        frappe.set_user(sender)
        with patch("aos.api.live.share.rate_limit", return_value=None):
            shared = self._share_live_to_chat(
                live_id=live.name,
                conversation_id=conversation.name,
                message="Come watch",
                idempotency_key="live-share-1",
            )
        self.assertTrue(shared.get("ok"), shared)
        message = shared["data"]["message"]
        self.assertEqual(message["live"], live.name)
        stored = frappe.db.get_value("AOS Message", message["id"], ["live", "content"], as_dict=True)
        self.assertEqual(stored.live, live.name)
        self.assertNotIn("/live/", str(stored.content or ""))

        frappe.db.set_value("AOS Live Stream", live.name, {"status": "ended", "is_active": 0}, update_modified=False)
        with patch("aos.api.live.share.rate_limit", return_value=None):
            rejected = self._share_live_to_chat(
                live_id=live.name,
                conversation_id=conversation.name,
                idempotency_key="live-share-2",
            )
        self.assertFalse(rejected.get("ok"), rejected)
        self.assertEqual(rejected.get("error"), "LIVE_CHAT_TARGET_NOT_FOUND")


if __name__ == "__main__":
    import unittest

    unittest.main()
