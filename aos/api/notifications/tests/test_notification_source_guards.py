from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestNotificationProductionSourceGuards(unittest.TestCase):
    def test_feature_documentation_is_single_canonical_file(self):
        docs = sorted(path.name for path in (ROOT / "docs/features/notifications").glob("*.md"))
        self.assertEqual(docs, ["README.md"])

    def test_public_surface_is_canonical_and_uses_shared_transport(self):
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
                "get_push_config",
                "register_push_token",
                "deactivate_push_token",
                "list_notifications",
                "mark_notification_read",
                "mark_all_notifications_read",
                "delete_notification",
                "clear_notifications",
                "handle_delivery_callback",
            },
        )
        self.assertEqual(source.count("_execute_endpoint("), 9)
        self.assertNotIn("_client_kwargs", source)
        self.assertIn('@frappe.whitelist(methods=["GET"])\ndef list_notifications', source)
        self.assertIn('@frappe.whitelist(allow_guest=True, methods=["POST"])\ndef handle_delivery_callback', source)

    def test_private_endpoints_are_rate_limited_no_store_and_do_not_own_full_transaction(self):
        inbox = _source("aos/api/notifications/notification.py")
        devices = _source("aos/api/notifications/token.py")
        push_config = _source("aos/api/notifications/push_config.py")
        self.assertEqual(inbox.count("set_private_no_store()"), 5)
        self.assertEqual(devices.count("set_private_no_store()"), 2)
        self.assertEqual(push_config.count("set_private_no_store()"), 1)
        self.assertIn("LIST_NOTIFICATIONS_LIMIT_PER_MINUTE_PER_USER", inbox)
        self.assertIn("REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER", devices)
        self.assertIn("GET_PUSH_CONFIG_LIMIT_PER_MINUTE_PER_USER", push_config)
        self.assertNotIn("frappe.db.commit", inbox)
        self.assertNotIn("frappe.db.commit", devices)
        self.assertNotIn("frappe.db.commit", push_config)
        self.assertNotIn("frappe.db.rollback()", inbox)
        self.assertNotIn("frappe.db.rollback()", devices)

    def test_inbox_is_owner_scoped_stably_paginated_and_public_safe(self):
        source = _source("aos/api/notifications/notification.py")
        self.assertIn("`user` = %s", source)
        self.assertIn("ORDER BY `creation` DESC, `name` DESC", source)
        self.assertIn("(`creation` < %s OR (`creation` = %s AND `name` < %s))", source)
        serializer = _source("aos/services/notifications/serializers.py")
        self.assertIn("serialize_notifications", source)
        self.assertIn("sanitize_public_payload", serializer)
        self.assertIn("get_user_display_map", serializer)
        self.assertIn("unread_count", source)
        for forbidden in ("token_hash", "request_payload", "response_payload", "service_job_id"):
            self.assertNotIn(f'\"{forbidden}\":', source)

    def test_notification_service_is_savepoint_isolated_deduplicated_and_not_a_business_state_owner(self):
        source = _source("aos/services/notifications/service.py")
        self.assertIn("frappe.db.savepoint(savepoint)", source)
        self.assertIn("is_duplicate_entry_error", source)
        self.assertIn("dedupe_key", source)
        self.assertIn("persistent_notification_suppression_reason", source)
        policy = _source("aos/services/notifications/policy.py")
        self.assertIn("is_blocked_between", policy)
        self.assertIn("get_account_state", policy)
        self.assertNotIn("frappe.db.commit", source)
        self.assertNotIn("frappe.db.rollback()", source)
        self.assertIn('event != "aos_incoming_call"', source)
        self.assertIn('type="missed_call"', source)
        self.assertIn("publish_created_after_commit", source)
        self.assertNotIn('type="call"', source)

    def test_delivery_jobs_use_transactional_outbox_and_safe_request_diagnostics(self):
        source = _source("aos/services/notifications/delivery.py")
        self.assertIn("ensure_outbox_for_job", source)
        self.assertIn("stable_idempotency_key", source)
        self.assertIn("current_outbox_dispatch_context", source)
        self.assertIn("record_companion_dispatch_outcome", source)
        self.assertIn("validate_callback_idempotency", source)
        self.assertIn('TRANSIENT_INCOMING_CALL_EVENT = "aos_incoming_call"', source)
        self.assertNotIn('data.get("call_id") or data.get("id")', source)
        self.assertIn("MAX_FCM_ESTIMATED_ENVELOPE_BYTES", source)
        self.assertIn("MAX_DELIVERY_TOKENS = 500", source)
        self.assertIn("limit=MAX_DELIVERY_TOKENS + 1", source)
        self.assertIn("_validate_fcm_envelope", source)
        self.assertIn('["caller", "receiver", "status"]', source)
        self.assertIn('relationship_suppression_reason(_clean(job.user), caller)', source)
        self.assertIn("persistent_notification_suppression_reason", source)
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
        self.assertIn("registration_kind", source)
        self.assertIn("normalize_registration_kind", source)
        self.assertIn("_deactivate_other_tokens_for_device", source)
        self.assertIn("owner != current_user", source)
        self.assertNotIn('"token": token', source.split("return ok(", 1)[1])
        observability = _source("aos/services/notifications/observability.py")
        self.assertIn('"token_fingerprint"', observability)
        self.assertNotIn('"token"', observability)
        self.assertNotIn('"payload"', observability)

    def test_push_token_registration_uses_narrow_row_locks_and_bounded_deadlock_retry(self):
        source = _source("aos/api/notifications/token.py")
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

        for function_name in ("_find_existing_token", "_find_existing_device"):
            sql_literals = [
                node.value
                for node in ast.walk(functions[function_name])
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "tabAOS Push Token" in node.value
            ]
            self.assertTrue(sql_literals)
            self.assertTrue(all("FOR UPDATE" not in sql.upper() for sql in sql_literals))

        lock_literals = [
            node.value
            for node in ast.walk(functions["_lock_push_token_row"])
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "tabAOS Push Token" in node.value
        ]
        self.assertEqual(len(lock_literals), 1)
        self.assertIn("WHERE name = %s", lock_literals[0])
        self.assertIn("FOR UPDATE", lock_literals[0].upper())
        self.assertNotIn("COALESCE(token_hash, '') = ''", source)
        self.assertIn("_REGISTER_DEADLOCK_ATTEMPTS = 3", source)
        self.assertIn("rollback_deadlocked_transaction()", source)

    def test_companion_request_models_are_strict_and_provider_retries_are_bounded(self):
        main = _source("infra/notification-delivery/app/main.py")
        worker = _source("infra/notification-delivery/app/worker.py")
        self.assertIn('extra="forbid"', main)
        self.assertIn('Literal["android", "ios", "web"]', main)
        self.assertIn('Literal["token", "fid"]', main)
        self.assertIn('Literal["persistent", "transient"]', main)
        self.assertIn('event != "aos_incoming_call"', main)
        self.assertIn("RetryableWorkError", worker)
        self.assertIn("retryable_failure_count", worker)
        self.assertIn("_rq_retries_left", worker)
        self.assertIn("FCM data payload is too large", worker)
        self.assertIn("FCM notification envelope is too large", worker)
        self.assertIn("_build_apns_config", worker)
        self.assertIn("_build_webpush_config", worker)
        self.assertIn('{"fids": target_values}', worker)
        self.assertIn('{"tokens": target_values}', worker)
        self.assertIn("callback_http_timeout_seconds", worker)
        self.assertIn('options={"httpTimeout": settings.provider_timeout_seconds}', worker)
        companion_config = _source("infra/notification-delivery/app/config.py")
        self.assertIn("provider_timeout_seconds", companion_config)
        self.assertIn("validate_firebase_configuration", companion_config)
        dispatch = _source("infra/notification-delivery/app/idempotent_dispatch.py")
        self.assertIn("provider_max_retries", dispatch)
        self.assertIn("_provider_retry_intervals", dispatch)
        self.assertNotIn('logger.exception("Notification delivery job failed")', worker)
        self.assertIn('error_class=%s', worker)

    def test_cross_service_callback_registry_tracks_notifications_canonical_callback_impl(self):
        source = _source("aos/tests/test_callback_atomicity_all_services.py")
        tree = ast.parse(source)
        adapter_class = next(
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CallbackAdapter"
        )
        fields = [
            node.target.id
            for node in adapter_class.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        ]
        self.assertEqual(
            fields,
            [
                "service_type",
                "api_module",
                "handler_name",
                "success_status",
                "failed_status",
                "endpoint_impl_name",
            ],
        )
        adapters_assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_ADAPTERS" for target in node.targets)
        )
        notification_adapter = next(
            element
            for element in adapters_assignment.value.elts
            if isinstance(element, ast.Call)
            and element.args
            and isinstance(element.args[0], ast.Constant)
            and element.args[0].value == "notification_delivery"
        )
        self.assertEqual(len(notification_adapter.args), 6)
        self.assertEqual(notification_adapter.args[2].value, "handle_notification_delivery_callback")
        self.assertEqual(notification_adapter.args[5].value, "handle_delivery_callback_impl")
        self.assertIn("getattr(adapter.api_module, adapter.endpoint_impl_name)", source)

    def test_terminal_delivery_jobs_use_identifier_snapshots_with_strict_live_validation(self):
        source = _source(
            "aos/aos/doctype/aos_notification_delivery_job/aos_notification_delivery_job.py"
        )
        doctype = json.loads(
            _source("aos/aos/doctype/aos_notification_delivery_job/aos_notification_delivery_job.json")
        )
        fields = {field["fieldname"]: field for field in doctype["fields"]}
        self.assertEqual(fields["user"]["fieldtype"], "Data")
        self.assertNotIn("options", fields["user"])
        self.assertEqual(fields["notification"]["fieldtype"], "Data")
        self.assertNotIn("options", fields["notification"])
        self.assertIn(
            'REFERENCE_TOLERANT_TERMINAL_STATUSES = frozenset({"Delivered", "Skipped", "Cancelled"})',
            source,
        )
        self.assertIn("not self.is_new()", source)
        self.assertIn("not user_exists and not terminal_reference_tolerant", source)
        self.assertIn("not notification_exists and not terminal_reference_tolerant", source)
        self.assertNotIn("self.flags.ignore_links = True", source)

    def test_delivery_callback_is_inside_notifications_and_strict(self):
        callback = _source("aos/api/notifications/callback.py")
        wrapper = _source("aos/api/v1/notifications/__init__.py")
        self.assertIn("_CALLBACK_FIELDS", callback)
        self.assertIn("Unsupported notification delivery callback field", callback)
        self.assertIn('error="DELIVERY_CALLBACK_INVALID"', callback)
        self.assertIn("handle_delivery_callback", wrapper)
        self.assertFalse((ROOT / "aos/api/v1/notification_delivery/__init__.py").exists())
        self.assertFalse((ROOT / "aos/api/notification_delivery/__init__.py").exists())
        self.assertFalse((ROOT / "aos/api/notification_delivery/callback.py").exists())

    def test_retention_is_bounded_and_index_backed(self):
        retention = _source("aos/services/notifications/retention.py")
        indexes = _source("aos/patches/v1_0/install_notification_indexes.py")
        hooks = _source("aos/hooks.py")
        self.assertIn("AOS_NOTIFICATION_INBOX_RETENTION_DAYS", retention)
        self.assertIn("AOS_NOTIFICATION_RETENTION_MAX_BATCHES", retention)
        self.assertIn("idx_aos_notification_retention", indexes)
        self.assertIn("idx_aos_notification_job_retention", indexes)
        self.assertIn("idx_aos_notification_job_notification_status", indexes)
        self.assertIn("j.notification = n.name", retention)
        self.assertIn("uq_aos_push_token_active_device", indexes)
        self.assertIn("aos.tasks.notifications.cleanup_notification_retention", hooks)

    def test_notification_center_realtime_is_recipient_scoped_post_commit_and_public_safe(self):
        realtime = _source("aos/services/notifications/realtime.py")
        serializer = _source("aos/services/notifications/serializers.py")
        service = _source("aos/services/notifications/service.py")
        inbox = _source("aos/api/notifications/notification.py")
        self.assertIn('EVENT_NOTIFICATION_CENTER = "aos_notification_center"', realtime)
        self.assertIn('frappe.db.after_commit.add(_safe_callback)', realtime)
        self.assertIn('user=user', realtime)
        self.assertNotIn('room=', realtime)
        self.assertIn('"action": "created"', realtime)
        self.assertIn('"unread_count"', realtime)
        self.assertIn("publish_created_after_commit", service)
        self.assertIn("persistent_notification_suppression_reason", realtime)
        self.assertIn("notification.realtime_suppressed", realtime)
        self.assertIn("publish_state_after_commit", inbox)
        for forbidden in ('"user":', '"email":', '"token":', '"token_hash":'):
            self.assertNotIn(forbidden, serializer)

    def test_web_push_bootstrap_exposes_only_public_firebase_material(self):
        config = _source("aos/services/notifications/web_push.py")
        endpoint = _source("aos/api/notifications/push_config.py")
        self.assertIn("NOTIFICATION_WEB_PUSH_ENABLED", config)
        self.assertIn("NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY", config)
        self.assertIn('"vapidPublicKey"', config)
        self.assertIn("require_login", endpoint)
        self.assertNotIn("SERVICE_ACCOUNT", endpoint)
        self.assertNotIn("PRIVATE_KEY", endpoint)

    def test_category_registry_matches_real_producers_including_short_mentions(self):
        contracts = _source("aos/services/notifications/contracts.py")
        notification_api = _source("aos/api/notifications/notification.py")
        mention_producer = _source("aos/api/shorts/mentions.py")
        self.assertIn('"short_mention": NotificationTypeContract', contracts)
        self.assertIn('event="aos_short_mention"', contracts)
        self.assertIn("from aos.services.notifications.contracts import", notification_api)
        self.assertIn("CATEGORY_TYPES", notification_api)
        self.assertIn("NotificationService.notify_short_mention", mention_producer)

    def test_account_deletion_cancels_delivery_revokes_access_and_defers_private_purge(self):
        tombstone = _source("aos/services/account_deletion_service.py")
        sessions = _source("aos/api/auth/session_control.py")
        self.assertIn("notification_delivery_jobs_cancelled", tombstone)
        self.assertIn("status = 'Cancelled'", tombstone)
        self.assertIn("push_tokens_removed", tombstone)
        self.assertIn("def _remove_push_tokens", tombstone)
        self.assertIn("revoke_push_tokens", sessions)
        self.assertIn("service_type = 'notification_delivery'", tombstone)

    def test_notification_new_site_migration_is_schema_only_and_reasserted_after_migrate(self):
        patches = _source("aos/patches.txt")
        index_name = "aos.patches.v1_0.install_notification_indexes"
        self.assertIn(index_name, patches)
        self.assertNotIn("harden_notification_subsystem", patches)
        self.assertNotIn("backfill_push_registration_kind", patches)
        self.assertFalse((ROOT / "aos/patches/v1_0/harden_notification_subsystem.py").exists())
        self.assertFalse((ROOT / "aos/patches/v1_0/backfill_push_registration_kind.py").exists())

        index_patch = _source("aos/patches/v1_0/install_notification_indexes.py")
        migrate = _source("aos/migrate.py")
        self.assertNotIn("frappe.db.commit", index_patch)
        self.assertIn("idx_aos_notification_user_timeline", index_patch)
        self.assertIn("idx_aos_notification_unread", index_patch)
        self.assertIn("idx_aos_notification_job_retry", index_patch)
        self.assertIn("idx_aos_notification_job_retention", index_patch)
        self.assertIn("uq_aos_push_token_active_device", index_patch)
        self.assertIn("install_notification_indexes.execute", migrate)

    def test_rate_limit_registry_covers_all_public_notification_endpoints(self):
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        endpoints = {row["endpoint"] for row in registry}
        expected = {
            "aos.api.v1.notifications.__init__.get_push_config",
            "aos.api.v1.notifications.__init__.register_push_token",
            "aos.api.v1.notifications.__init__.deactivate_push_token",
            "aos.api.v1.notifications.__init__.list_notifications",
            "aos.api.v1.notifications.__init__.mark_notification_read",
            "aos.api.v1.notifications.__init__.mark_all_notifications_read",
            "aos.api.v1.notifications.__init__.delete_notification",
            "aos.api.v1.notifications.__init__.clear_notifications",
            "aos.api.v1.notifications.__init__.handle_delivery_callback",
        }
        self.assertTrue(expected <= endpoints)


if __name__ == "__main__":
    unittest.main()
