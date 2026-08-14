from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestNotificationProductionSourceGuards(unittest.TestCase):
    def test_public_surface_preserves_existing_endpoints_and_strips_transport_fields(self):
        source = _source("aos/api/v1/notifications/__init__.py")
        tree = ast.parse(source)
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "whitelist"
                for decorator in node.decorator_list
            )
        }
        self.assertEqual(
            functions,
            {
                "register_push_token",
                "deactivate_push_token",
                "list_notifications",
                "mark_notification_read",
                "mark_all_notifications_read",
                "delete_notification",
                "clear_notifications",
            },
        )
        self.assertEqual(source.count("_client_kwargs(kwargs)"), 7)

    def test_private_endpoints_are_rate_limited_no_store_and_do_not_own_full_transaction(self):
        inbox = _source("aos/api/notifications/notification.py")
        devices = _source("aos/api/notifications/token.py")
        self.assertEqual(inbox.count("set_private_no_store()"), 5)
        self.assertEqual(devices.count("set_private_no_store()"), 2)
        self.assertIn("LIST_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER", inbox)
        self.assertIn("REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER", devices)
        self.assertNotIn("frappe.db.commit", inbox)
        self.assertNotIn("frappe.db.commit", devices)
        self.assertNotIn("frappe.db.rollback()", inbox)
        self.assertNotIn("frappe.db.rollback()", devices)

    def test_inbox_is_owner_scoped_stably_paginated_and_public_safe(self):
        source = _source("aos/api/notifications/notification.py")
        self.assertIn("`user` = %s", source)
        self.assertIn("ORDER BY `creation` DESC, `name` DESC", source)
        self.assertIn("(`creation` < %s OR (`creation` = %s AND `name` < %s))", source)
        self.assertIn("sanitize_public_payload", source)
        self.assertIn("get_user_display_map", source)
        for forbidden in ("token_hash", "request_payload", "response_payload", "service_job_id"):
            self.assertNotIn(f'\"{forbidden}\":', source)

    def test_notification_service_is_savepoint_isolated_deduplicated_and_not_a_business_state_owner(self):
        source = _source("aos/services/notification_service.py")
        self.assertIn("frappe.db.savepoint(savepoint)", source)
        self.assertIn("is_duplicate_entry_error", source)
        self.assertIn("dedupe_key", source)
        self.assertIn("is_blocked_between", source)
        self.assertNotIn("frappe.db.commit", source)
        self.assertNotIn("frappe.db.rollback()", source)
        self.assertIn('event != "aos_incoming_call"', source)
        self.assertIn('type="missed_call"', source)
        self.assertNotIn('type="call"', source)

    def test_delivery_jobs_use_transactional_outbox_and_safe_request_diagnostics(self):
        source = _source("aos/services/notification_delivery_service.py")
        self.assertIn("ensure_outbox_for_job", source)
        self.assertIn("stable_idempotency_key", source)
        self.assertIn("current_outbox_dispatch_context", source)
        self.assertIn("record_companion_dispatch_outcome", source)
        self.assertIn("validate_callback_idempotency", source)
        self.assertIn('TRANSIENT_INCOMING_CALL_EVENT = "aos_incoming_call"', source)
        self.assertIn('["caller", "receiver", "status"]', source)
        self.assertIn('is_blocked_between(job.user, caller)', source)
        self.assertIn('def _safe_callback_reason', source)
        self.assertIn('fallback="notification_delivery_failed"', source)
        sanitizer = source.split("def _sanitize_payload", 1)[1].split("def _bounded_json_object", 1)[0]
        for private in ('"title"', '"body"', '"data"', '"token"', '"callback_url"'):
            self.assertNotIn(private, sanitizer)
        self.assertIn('"token_hash"', sanitizer)
        retry = source.split("def retry_queued_notification_delivery_jobs", 1)[1]
        self.assertIn('"status": "Queued"', retry)
        self.assertNotIn('"Failed"', retry)

    def test_device_registration_is_owner_scoped_transfer_safe_and_token_logs_are_fingerprinted(self):
        source = _source("aos/api/notifications/token.py")
        self.assertIn("get_token_hash", source)
        self.assertIn("token_fingerprint", source)
        self.assertIn("_deactivate_other_tokens_for_device", source)
        self.assertIn("owner != current_user", source)
        self.assertNotIn('"token": token', source.split("return ok(", 1)[1])
        observability = _source("aos/services/notifications/observability.py")
        self.assertIn('"token_fingerprint"', observability)
        self.assertNotIn('"token"', observability)
        self.assertNotIn('"payload"', observability)

    def test_companion_request_models_are_strict_and_provider_retries_are_bounded(self):
        main = _source("infra/notification-delivery/app/main.py")
        worker = _source("infra/notification-delivery/app/worker.py")
        self.assertIn('extra="forbid"', main)
        self.assertIn('Literal["android", "ios", "web"]', main)
        self.assertIn('Literal["persistent", "transient"]', main)
        self.assertIn('event != "aos_incoming_call"', main)
        self.assertIn("RetryableWorkError", worker)
        self.assertIn("retryable_failure_count", worker)
        self.assertIn("_rq_retries_left", worker)
        self.assertIn("FCM data payload is too large", worker)
        self.assertNotIn('logger.exception("Notification delivery job failed")', worker)
        self.assertIn('error_class=%s', worker)

    def test_category_registry_matches_real_producers_including_short_mentions(self):
        contracts = _source("aos/services/notifications/contracts.py")
        constants = _source("aos/api/notifications/constants.py")
        mention_producer = _source("aos/api/shorts/mentions.py")
        self.assertIn('"short_mention": NotificationTypeContract', contracts)
        self.assertIn('event="aos_short_mention"', contracts)
        self.assertIn("CATEGORY_TYPES", constants)
        self.assertIn("NotificationService.notify_short_mention", mention_producer)

    def test_account_deletion_revokes_delivery_and_removes_private_device_tokens(self):
        source = _source("aos/services/account_deletion_service.py")
        self.assertIn("notification_delivery_jobs_cancelled", source)
        self.assertIn("push_tokens_removed", source)
        self.assertIn("status = 'Cancelled'", source)
        self.assertIn('def _remove_push_tokens', source)
        self.assertIn('def _deactivate_push_tokens', source)
        self.assertIn('"AOS Push Token",', source.split('def _remove_push_tokens', 1)[1].split('_CHAT_PRIVATE_USER_FIELDS', 1)[0])
        self.assertIn('service_type = \'notification_delivery\'', source)

    def test_notification_migrations_are_registered_data_before_schema(self):
        patches = _source("aos/patches.txt")
        data_name = "aos.patches.v1_0.harden_notification_subsystem"
        index_name = "aos.patches.v1_0.install_notification_indexes"
        self.assertIn(data_name, patches)
        self.assertIn(index_name, patches)
        self.assertLess(patches.index(data_name), patches.index(index_name))
        data_patch = _source("aos/patches/v1_0/harden_notification_subsystem.py")
        index_patch = _source("aos/patches/v1_0/install_notification_indexes.py")
        self.assertNotIn("frappe.reload_doc", data_patch)
        self.assertNotIn("frappe.db.commit", data_patch)
        self.assertNotIn("frappe.db.commit", index_patch)
        token_normalizer = data_patch.split("def _normalize_push_tokens", 1)[1].split("def _cancel_undeliverable_jobs", 1)[0]
        self.assertIn("if not names:\n            break", token_normalizer)
        self.assertIn("token_hash == hashlib.sha256", token_normalizer)
        self.assertIn("idx_aos_notification_user_timeline", index_patch)
        self.assertIn("idx_aos_notification_unread", index_patch)
        self.assertIn("idx_aos_notification_job_retry", index_patch)

    def test_rate_limit_registry_covers_all_public_notification_endpoints(self):
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        endpoints = {row["endpoint"] for row in registry}
        expected = {
            "aos.api.v1.notifications.__init__.register_push_token",
            "aos.api.v1.notifications.__init__.deactivate_push_token",
            "aos.api.v1.notifications.__init__.list_notifications",
            "aos.api.v1.notifications.__init__.mark_notification_read",
            "aos.api.v1.notifications.__init__.mark_all_notifications_read",
            "aos.api.v1.notifications.__init__.delete_notification",
            "aos.api.v1.notifications.__init__.clear_notifications",
        }
        self.assertTrue(expected <= endpoints)


if __name__ == "__main__":
    unittest.main()
