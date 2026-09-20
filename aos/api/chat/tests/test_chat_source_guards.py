from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CHAT_SCOPE = (
    ROOT / "aos/api/chat",
    ROOT / "aos/api/v1/chat",
    ROOT / "aos/services/chat",
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class TestChatSourceGuards(unittest.TestCase):
    def test_public_v1_surface_is_complete_and_thin(self):
        source = _source("aos/api/v1/chat/__init__.py")
        endpoints = re.findall(r"^def ([a-z_]+)\(\*\*kwargs\):", source, flags=re.MULTILINE)
        self.assertEqual(len(endpoints), 17)
        self.assertEqual(len(endpoints), len(set(endpoints)))
        self.assertIn("run_chat_api(", source)
        self.assertIn("ENDPOINT_SPECS[name]", source)

    def test_no_internal_commit_or_whole_transaction_rollback(self):
        violations: list[str] = []
        paths = []
        for root in CHAT_SCOPE:
            paths.extend(root.rglob("*.py"))
        paths.extend(
            [
                ROOT / "aos/patches/v1_0/harden_chat_subsystem.py",
                ROOT / "aos/patches/v1_0/install_chat_indexes.py",
            ]
        )
        for path in paths:
            if "tests" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "frappe.db.commit(" in source:
                violations.append(f"{path.relative_to(ROOT)}: commit")
            if "frappe.db.rollback()" in source:
                violations.append(f"{path.relative_to(ROOT)}: rollback")
        self.assertEqual(violations, [])
        api = _source("aos/services/chat/api.py")
        self.assertIn("rollback(save_point=savepoint)", api)

    def test_savepoint_boundary_preserves_outer_callbacks(self):
        source = _source("aos/services/chat/api.py")
        self.assertIn("_snapshot_callbacks", source)
        self.assertIn("_restore_callbacks", source)
        self.assertIn("_restore_outbox_flag", source)
        self.assertIn("rollback(save_point=savepoint)", source)

    def test_native_live_message_and_feature_owned_share_exist(self):
        message_json = json.loads(_source("aos/aos/doctype/aos_message/aos_message.json"))
        message_type = next(field for field in message_json["fields"] if field.get("fieldname") == "message_type")
        self.assertIn("live", str(message_type.get("options") or "").lower().splitlines())
        live_field = next(field for field in message_json["fields"] if field.get("fieldname") == "live")
        self.assertEqual(live_field.get("options"), "AOS Live Stream")
        chat = _source("aos/api/chat/message.py")
        self.assertIn('"live_preview"', chat)
        self.assertIn('"live_unavailable"', chat)
        self.assertIn("LivePolicy", chat)
        live_share = _source("aos/api/live/share.py")
        self.assertIn("ChatService", live_share)
        self.assertIn("send_live_reference", live_share)
        live_v1 = _source("aos/api/v1/live/__init__.py")
        self.assertIn("def share_live_to_chat", live_v1)

    def test_shorts_share_boundary_is_chat_ready_without_a_client_shortcut(self):
        chat_service = _source("aos/services/chat/service.py")
        shorts_v1 = _source("aos/api/v1/shorts/__init__.py")
        self.assertIn("def send_short_reference", chat_service)
        self.assertNotIn("share_short_to_chat", shorts_v1)
        self.assertNotIn("ChatService", shorts_v1)

    def test_persistent_chat_realtime_is_after_commit(self):
        for relative in (
            "aos/api/chat/message.py",
            "aos/api/chat/forward_message.py",
            "aos/api/chat/edit_message.py",
            "aos/api/chat/delete_messages.py",
            "aos/api/chat/reactions.py",
            "aos/api/chat/status.py",
        ):
            source = _source(relative)
            self.assertIn("publish_after_commit", source, relative)
        event_helper = _source("aos/services/chat/events.py")
        self.assertIn("manager.add(callback)", event_helper)
        self.assertIn("frappe.publish_realtime", event_helper)

    def test_public_error_paths_do_not_return_raw_exception_strings(self):
        offenders = []
        for root in CHAT_SCOPE:
            for path in root.rglob("*.py"):
                if "tests" in path.parts:
                    continue
                source = path.read_text(encoding="utf-8")
                if "fail(str(exc)" in source or "fail(str(exception)" in source:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_chat_logs_do_not_capture_raw_tracebacks_or_payload_values(self):
        offenders = []
        for path in (ROOT / "aos/api/chat").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "frappe.get_traceback()" in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
        observability = _source("aos/services/chat/observability.py")
        for private_field in ("account_id", "message_id", "conversation_id", "cursor", "comment_text", "ip_address"):
            self.assertNotIn(f'"{private_field}"', observability)

    def test_bulk_history_mutations_are_bounded(self):
        clear = _source("aos/api/chat/clear_chat.py")
        self.assertIn("CLEAR_CHAT_BATCH_SIZE = 500", clear)
        self.assertIn("LIMIT %(limit)s", clear)
        self.assertIn("_clear_visible_messages_bounded", clear)
        status = _source("aos/api/chat/status.py")
        self.assertIn("batch_size = 500", status)
        self.assertIn("LIMIT %(limit)s", status)
        endpoints = _source("aos/services/chat/endpoints.py")
        self.assertIn('"send_typing_event"', endpoints.split("TRANSACTIONAL_ENDPOINTS", 1)[1])

    def test_chat_api_sql_has_no_f_string_queries(self):
        offenders = []
        pattern = re.compile(r"frappe\.db\.sql\(\s*f(?:\"\"\"|'''|\"|')", re.MULTILINE)
        for path in (ROOT / "aos/api/chat").rglob("*.py"):
            if "tests" in path.parts:
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_chat_uniqueness_and_indexes_are_migration_ordered(self):
        patches = _source("aos/patches.txt")
        data = patches.index("aos.patches.v1_0.harden_chat_subsystem")
        indexes = patches.index("aos.patches.v1_0.install_chat_indexes")
        self.assertLess(data, indexes)
        installer = _source("aos/patches/v1_0/install_chat_indexes.py")
        self.assertIn("pair_key", installer)
        self.assertIn("idempotency_key", installer)
        self.assertIn("message", installer)
        self.assertIn("media", installer)
        self.assertNotIn("frappe.db.commit", installer)
        migration = _source("aos/patches/v1_0/harden_chat_subsystem.py")
        self.assertIn("BATCH_SIZE", migration)
        self.assertIn("_deactivate_unavailable_participants", migration)
        self.assertIn("unavailable_participants_deactivated", migration)
        self.assertNotIn("LiveKitAPI", migration)
        self.assertNotIn("frappe.enqueue", migration)
        self.assertNotIn("frappe.db.commit", migration)

    def test_chat_rate_keys_and_registry_are_private_and_complete(self):
        for path in (ROOT / "aos/api/chat").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "rate_limit(" in source:
                self.assertIn("rate_limit_key", source, str(path.relative_to(ROOT)))
                self.assertNotIn('key=f"aos:chat:', source)
        entries = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        registry = {entry["endpoint"]: entry for entry in entries}
        for endpoint in (
            "open_conversation", "list_conversations", "send_message", "list_messages",
            "forward_message", "edit_message", "delete_messages", "toggle_message_reaction",
            "translate_message", "mark_delivered", "mark_read", "send_typing_event", "get_presence",
        ):
            self.assertIn(f"aos.api.v1.chat.__init__.{endpoint}", registry)
        self.assertIn("aos.api.v1.live.__init__.share_live_to_chat", registry)

    def test_notification_payload_uses_public_sender_and_dedupe(self):
        source = _source("aos/services/notifications/service.py")
        block = source.split("def notify_new_message", 1)[1].split("def ", 1)[0]
        self.assertIn("public_account_id_for_user", block)
        self.assertIn("dedupe_key", block)
        self.assertIn("message_id", block)

    def test_short_previews_use_batch_visibility_policy(self):
        source = _source("aos/api/chat/message.py")
        block = source.split("def _fetch_shorts_bulk", 1)[1].split("def _can_view_short", 1)[0]
        self.assertIn("filter_viewable_rows", block)
        self.assertNotIn("_can_view_short(", block)

    def test_ad_previews_and_forwards_apply_marketplace_visibility_policy(self):
        shared = _source("aos/services/chat/shared_objects.py")
        self.assertIn("fetch_chat_ad_previews", shared)
        self.assertIn("expires_on", shared)
        self.assertIn('seller.get("status")', shared)
        self.assertIn("ACCOUNT_STATUS_ACTIVE", shared)
        self.assertIn("get_blocked_user_set", shared)
        message = _source("aos/api/chat/message.py")
        self.assertIn('"ad_unavailable"', message)
        self.assertIn("ad_is_shareable_to_users", message)
        forward = _source("aos/api/chat/forward_message.py")
        self.assertIn("_validate_forwarded_ad_access", forward)
        self.assertIn("ad_error = _validate_forwarded_ad_access", forward)

    def test_permanent_account_cleanup_clears_private_chat_state_without_commits(self):
        source = _source("aos/services/account_deletion_service.py")
        self.assertIn("_cleanup_chat_private_state", source)
        self.assertIn("chat_stars_removed", source)
        self.assertIn("chat_reactions_removed", source)
        self.assertIn("chat_translation_cache_removed", source)
        self.assertIn("_CHAT_PRIVATE_USER_FIELDS", source)
        self.assertIn('"AOS Message Star": "user"', source)


    def test_new_live_messages_require_active_live_and_forward_realtime_is_receiver_aware(self):
        message = _source("aos/api/chat/message.py")
        send_block = message.split("def send_message_impl", 1)[1].split("# list_messages", 1)[0]
        self.assertIn("require_live_active=True", send_block)
        forward = _source("aos/api/chat/forward_message.py")
        self.assertIn("serialized_for_receiver", forward)
        self.assertIn('"message": serialized_for_receiver', forward)
        self.assertIn('"message": serialized_for_sender', forward)

    def test_transient_chat_interactions_respect_social_blocks(self):
        presence = _source("aos/api/chat/presence.py")
        typing_block = presence.split("def send_typing_event_impl", 1)[1]
        self.assertIn("get_blocked_user_set", typing_block)
        self.assertIn('return fail("Not allowed.", error="PERMISSION_DENIED", http_status=403)', typing_block)
        self.assertIn("def get_presence_impl", presence)
        self.assertIn("_presence_payload(peer)", presence)
        self.assertIn("GET_PRESENCE_LIMIT_PER_MINUTE_PER_USER", presence)
        reactions = _source("aos/api/chat/reactions.py")
        validation = reactions.split("def _validate_message_can_be_reacted_to", 1)[1].split("def _validate_emoji", 1)[0]
        self.assertIn("get_blocked_user_set", validation)

    def test_feature_owned_chat_service_preserves_stable_error_categories(self):
        service = _source("aos/services/chat/service.py")
        for code in (
            "CHAT_NOT_FOUND", "CHAT_ACCESS_DENIED", "CHAT_INVALID_REQUEST",
            "CHAT_INPUT_TOO_LARGE", "CHAT_CONFLICT", "CHAT_INVALID_STATE",
            "CHAT_RATE_LIMITED", "CHAT_DEPENDENCY_UNAVAILABLE", "CHAT_INTERNAL_ERROR",
        ):
            self.assertIn(f'"{code}"', service)
        self.assertNotIn('response.get("http_status")', service)

    def test_required_chat_documents_exist(self):
        for name in (
            "README.md", "architecture.md", "api.md", "messages.md", "sharing.md",
            "realtime.md", "presence.md", "attachments.md", "privacy.md",
            "notifications.md", "migration.md", "operations.md", "testing.md",
        ):
            self.assertTrue((ROOT / "docs/features/chat" / name).is_file(), name)



    def test_starred_messages_expose_public_conversation_id_for_navigation(self):
        source = _source("aos/api/chat/stars.py")
        self.assertIn('payload["conversation_id"] = msg.conversation', source)

if __name__ == "__main__":
    unittest.main()
