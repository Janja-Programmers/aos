from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
CHAT_RUNTIME = ROOT / "aos/services/chat"


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class TestChatSourceGuards(unittest.TestCase):
    def test_public_v1_surface_is_complete_and_thin(self):
        source = _source("aos/api/v1/chat/__init__.py")
        endpoints = set(re.findall(r"^def ([a-z_]+)\(\*\*kwargs\):", source, flags=re.MULTILINE))
        expected = {
            "open_conversation", "create_group", "update_group", "add_group_members",
            "remove_group_member", "set_group_member_role", "transfer_group_ownership",
            "leave_group", "list_group_members", "list_conversations", "list_locked_conversations",
            "delete_conversation", "set_conversation_lock", "configure_chat_lock_secret",
            "verify_chat_lock_secret", "remove_chat_lock_secret", "get_chat_lock_state",
            "send_message", "list_messages", "forward_message", "edit_message", "delete_messages",
            "clear_chat", "set_message_star", "list_starred_messages", "set_message_reaction",
            "translate_message", "mark_delivered", "mark_read", "send_typing_event", "get_presence",
        }
        self.assertEqual(endpoints, expected)
        self.assertIn("run_chat_api(", source)
        self.assertIn("ENDPOINT_SPECS[name]", source)

    def test_old_runtime_wrappers_are_removed(self):
        runtime_files = sorted(
            p.name for p in (ROOT / "aos/api/chat").glob("*.py") if p.name != "__init__.py"
        )
        self.assertEqual(runtime_files, [])

    def test_normalized_group_schema_is_authoritative(self):
        conversation = json.loads(_source("aos/aos/doctype/aos_conversation/aos_conversation.json"))
        participant = json.loads(_source("aos/aos/doctype/aos_conversation_participant/aos_conversation_participant.json"))
        message = json.loads(_source("aos/aos/doctype/aos_message/aos_message.json"))
        cfields = {f["fieldname"]: f for f in conversation["fields"]}
        pfields = {f["fieldname"]: f for f in participant["fields"]}
        mfields = {f["fieldname"]: f for f in message["fields"]}
        self.assertEqual(cfields["conversation_type"]["options"].splitlines(), ["direct", "group"])
        self.assertIn("direct_key", cfields)
        self.assertNotIn("participant_1", cfields)
        self.assertNotIn("participant_2", cfields)
        for field in ("conversation", "user", "role", "status", "visible_from", "unread_count", "is_locked"):
            self.assertIn(field, pfields)
        self.assertIn("recipient_count", mfields)
        self.assertNotIn("delivered_to_receiver_at", mfields)

    def test_group_membership_is_bounded_and_owner_safe(self):
        source = _source("aos/services/chat/membership.py")
        self.assertIn("MAX_GROUP_PARTICIPANTS = 256", source)
        self.assertIn("def transfer_owner_if_needed", source)
        self.assertIn("def deactivate_account_memberships", source)
        conversation = _source("aos/services/chat/conversation_ops.py")
        self.assertIn("assert_group_manager", conversation)
        self.assertIn("visible_from=now_datetime()", conversation)
        deletion = _source("aos/services/account_deletion_service.py")
        purge = _source("aos/services/account_purge_service.py")
        self.assertIn("deactivate_account_memberships", deletion)
        self.assertIn("deactivate_account_memberships", purge)

    def test_chat_lock_is_per_user_hashed_and_redis_tokenized(self):
        source = _source("aos/services/chat/lock.py")
        self.assertIn("passlibctx.hash", source)
        self.assertIn("passlibctx.verify", source)
        self.assertIn("SELECT name FROM `tabUser` WHERE name=%s LIMIT 1 FOR UPDATE", source)
        self.assertIn("ACCESS_TOKEN_TTL_SECONDS = 300", source)
        self.assertIn("frappe.cache()", source)
        self.assertNotIn("doc.secret =", source)
        participant = json.loads(_source("aos/aos/doctype/aos_conversation_participant/aos_conversation_participant.json"))
        self.assertIn("is_locked", {f["fieldname"] for f in participant["fields"]})

    def test_hidden_locked_chats_fail_closed_and_notifications_are_private(self):
        lock = _source("aos/services/chat/lock.py")
        ops = _source("aos/services/chat/lock_ops.py")
        message = _source("aos/services/chat/message_ops.py")
        notification = _source("aos/services/notifications/service.py")
        self.assertIn('code="CHAT_NOT_FOUND"', lock)
        self.assertIn('"locked_conversation_count": locked_count', ops)
        self.assertIn('"hidden": bool(', ops)
        self.assertIn("private_preview=recipient in locked", message)
        self.assertIn("private_preview", notification)
        self.assertIn("New message in a locked chat", notification)

    def test_locked_group_events_are_redacted(self):
        source = _source("aos/services/chat/conversation_ops.py")
        self.assertIn("def _publish_group_update", source)
        self.assertIn('message = {"conversation_id": conversation_id, "change": "refresh"}', source)

    def test_persistent_realtime_is_after_commit(self):
        helper = _source("aos/services/chat/events.py")
        self.assertIn("manager.add(callback)", helper)
        self.assertIn("frappe.publish_realtime", helper)
        for relative in (
            "aos/services/chat/message_ops.py",
            "aos/services/chat/message_mutations.py",
            "aos/services/chat/status_ops.py",
            "aos/services/chat/conversation_ops.py",
        ):
            self.assertIn("publish_after_commit", _source(relative), relative)

    def test_idempotency_and_retry_sensitive_mutations_are_safe(self):
        message = _source("aos/services/chat/message_ops.py")
        mutations = _source("aos/services/chat/message_mutations.py")
        self.assertIn("idempotency_request_hash", message)
        self.assertIn('error="CHAT_CONFLICT"', message)
        self.assertIn("lock_conversations([conv_id]); lock_messages([mid])", mutations)
        self.assertIn("set_message_star_impl", mutations)
        self.assertIn("set_message_reaction_impl", mutations)
        self.assertIn('derived=f"{key}:{source_id}:{idx}:{target}"', mutations)

    def test_status_updates_are_bounded_and_report_continuation(self):
        source = _source("aos/services/chat/status_ops.py")
        self.assertIn("BATCH_SIZE = 500", source)
        self.assertIn("MAX_BATCHES_PER_REQUEST = 4", source)
        self.assertIn('"has_more": has_more', source)
        self.assertNotIn('"unread_count", 0', source)

    def test_shared_objects_use_feature_owned_visibility(self):
        projections = _source("aos/services/chat/projections.py")
        shared = _source("aos/services/chat/shared_objects.py")
        self.assertIn("filter_viewable_rows", projections)
        self.assertIn("LivePolicy", projections)
        self.assertIn("fetch_chat_ad_previews", projections)
        self.assertIn("ad_is_shareable_to_users", shared)
        self.assertIn('"short_unavailable"', projections)
        self.assertIn('"live_unavailable"', projections)
        self.assertIn('"ad_unavailable"', projections)

    def test_forward_provenance_is_not_public(self):
        serializer = _source("aos/services/chat/projections.py").split("def serialize_messages", 1)[1]
        base = serializer.split("if int(row.deleted_for_everyone", 1)[0]
        self.assertIn('"conversation_id": row.conversation', base)
        self.assertNotIn('"forwarded_from_message"', base)
        self.assertNotIn('"forwarded_from_conversation"', base)

    def test_calls_bind_only_to_exact_chat_membership_context(self):
        calls = "\n".join(
            _source(path)
            for path in (
                "aos/api/calls/validators.py",
                "aos/api/calls/utils.py",
                "aos/api/calls/call.py",
            )
        )
        self.assertIn("validate_conversation_for_call", calls)
        self.assertIn("conversation_type", calls)
        self.assertIn('{"direct", "group"}', calls)
        self.assertIn("AOS Conversation Participant", calls)
        self.assertIn("Conversation participants do not match this call", calls)

    def test_translation_cache_is_source_aware_and_german_supported(self):
        indexes = _source("aos/patches/v1_0/install_chat_indexes.py")
        translation = _source("aos/services/chat/translation_ops.py")
        languages = _source("infra/translation/app/languages.py")
        self.assertIn("uniq_chat_translation_request_cache", indexes)
        self.assertIn("request_source_language", translation)
        self.assertIn("request_target_language", translation)
        self.assertIn('"de": LanguageInfo("deu_Latn", "German")', languages)
        self.assertNotIn('"translated_by":', translation.split("def _serialize", 1)[1].split("def translate_message_impl", 1)[0])

    def test_translation_client_uses_aos_runtime_config_fallback_for_shared_secret(self):
        client = _source("aos/integrations/ai/translation_client.py")
        self.assertIn('get_env("TRANSLATION_INTERNAL_TOKEN"', client)
        self.assertNotIn('os.getenv("TRANSLATION_INTERNAL_TOKEN"', client)
        production = _source("aos/utils/production_config.py")
        self.assertIn('keys=("TRANSLATION_INTERNAL_TOKEN",)', production)

    def test_group_membership_changes_are_durable_system_messages(self):
        ops = _source("aos/services/chat/conversation_ops.py")
        system = _source("aos/services/chat/system_messages.py")
        self.assertIn("group_system_message", ops)
        self.assertIn("created the group", ops)
        self.assertIn("group admin", ops)
        self.assertIn('msg.message_type = "system"', system)
        self.assertIn('event="aos_new_message"', system)

    def test_translation_service_fails_closed_and_runtime_is_bounded(self):
        main = _source("infra/translation/app/main.py")
        runtime = _source("infra/translation/app/translator.py")
        dockerfile = _source("infra/translation/Dockerfile")
        self.assertIn("Service authentication is not configured", main)
        self.assertIn('Depends(_internal_auth)', main)
        self.assertIn("BoundedSemaphore", runtime)
        self.assertIn("max_source_tokens", runtime)
        self.assertIn("max_decoding_length", runtime)
        self.assertIn("USER 10001:10001", dockerfile)

    def test_no_internal_commit_or_whole_transaction_rollback(self):
        violations: list[str] = []
        for path in CHAT_RUNTIME.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "frappe.db.commit(" in source:
                violations.append(f"{path.relative_to(ROOT)}: commit")
            if "frappe.db.rollback()" in source:
                violations.append(f"{path.relative_to(ROOT)}: rollback")
        self.assertEqual(violations, [])
        api = _source("aos/services/chat/api.py")
        self.assertIn("rollback(save_point=savepoint)", api)

    def test_public_errors_do_not_return_raw_exception_strings(self):
        offenders = []
        for path in CHAT_RUNTIME.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "fail(str(exc)" in source or "fail(str(exception)" in source or "frappe.get_traceback()" in source:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])

    def test_rate_limit_registry_covers_every_chat_endpoint(self):
        source = _source("aos/api/v1/chat/__init__.py")
        endpoints = set(re.findall(r"^def ([a-z_]+)\(\*\*kwargs\):", source, flags=re.MULTILINE))
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        registered = {
            row["endpoint"].rsplit(".", 1)[-1]
            for row in registry
            if row.get("endpoint", "").startswith("aos.api.v1.chat.__init__.")
        }
        self.assertEqual(registered, endpoints)

    def test_chat_has_one_authoritative_feature_document(self):
        chat_docs = ROOT / "docs/features/chat"
        self.assertTrue((chat_docs / "README.md").is_file())
        self.assertEqual(sorted(p.name for p in chat_docs.iterdir() if p.is_file()), ["README.md"])


if __name__ == "__main__":
    unittest.main()
