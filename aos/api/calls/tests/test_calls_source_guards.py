from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CALL_SCOPE = (ROOT / "aos/api/calls", ROOT / "aos/services/calls")


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestCallsProductionSourceGuards(unittest.TestCase):
    def test_public_v1_surface_is_thin_strict_and_complete(self):
        source = _source("aos/api/v1/calls/__init__.py")
        for endpoint in (
            "initiate_call", "mark_call_ringing", "accept_call", "reject_call", "cancel_call", "end_call",
            "request_video_upgrade", "respond_video_upgrade", "get_call_status", "get_call_token", "list_calls",
            "get_call_group_details", "delete_call_logs", "clear_call_history",
        ):
            self.assertIn(f"def {endpoint}", source)
            self.assertIn(f'"{endpoint}"', source)
        self.assertIn("run_call_api", source)
        self.assertIn("ENDPOINT_SPECS", source)
        self.assertIn("_client_kwargs", source)

    def test_calls_mutation_modules_do_not_commit_or_rollback_outer_transactions(self):
        offenders = []
        for root in CALL_SCOPE:
            for path in root.rglob("*.py"):
                if "tests" in path.parts or path.name == "api.py":
                    continue
                source = path.read_text(encoding="utf-8")
                if "frappe.db.commit(" in source or "frappe.db.rollback(" in source:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
        boundary = _source("aos/services/calls/api.py")
        self.assertIn("savepoint", boundary)
        self.assertIn("rollback(save_point=savepoint)", boundary)
        self.assertNotIn("frappe.db.commit(", boundary)

    def test_realtime_is_after_commit_and_payload_identity_is_public(self):
        source = _source("aos/api/calls/realtime.py")
        tree = ast.parse(source)
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "publish_realtime"
        ]
        self.assertGreater(len(calls), 0)
        for call in calls:
            keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            self.assertIn("after_commit", keywords)
            self.assertIsInstance(keywords["after_commit"], ast.Constant)
            self.assertTrue(keywords["after_commit"].value)
        self.assertIn('"ended_by": ended_by.get("account_id")', source)
        self.assertIn('"video_upgrade_requested_by": upgrade_requester.get("account_id")', source)
        self.assertNotIn('"ended_by": call.ended_by', source)

    def test_call_token_requires_participant_state_and_public_identity(self):
        token = _source("aos/api/calls/token.py")
        self.assertIn('current_user == call.receiver and call.status != "ongoing"', token)
        self.assertIn("participant_identity(current_user)", token)
        self.assertIn("ensure_call_interaction_allowed", token)
        self.assertIn("clear_missing_room_marker", token)
        livekit = _source("aos/services/calls/livekit.py")
        self.assertIn("public_account_id_for_user", livekit)
        self.assertIn("CALL_DEPENDENCY_UNAVAILABLE", livekit)
        service = _source("aos/services/livekit_service.py")
        ttl = service.split("def _get_token_ttl", 1)[1].split("def _get_live_token_ttl", 1)[0]
        self.assertIn("min(ttl_minutes, 5)", ttl)

    def test_calls_use_opaque_public_ids_and_do_not_expose_room_names(self):
        identifiers = _source("aos/services/calls/identifiers.py")
        validation = _source("aos/services/calls/validation.py")
        self.assertIn(r'^call_[0-9a-f]{32}$', identifiers)
        self.assertIn("CALL_ID_RE = PUBLIC_CALL_ID_RE", validation)
        realtime = _source("aos/api/calls/realtime.py")
        self.assertIn("public_call_id(call)", realtime)
        serializer = realtime.split("def serialize_call_for_realtime", 1)[1].split("def _publish", 1)[0]
        self.assertNotIn('"room_name"', serializer)
        self.assertIn('"state_version"', serializer)
        self.assertIn('"rtc_ready"', serializer)

    def test_room_is_provisioned_before_incoming_delivery_and_token_join(self):
        call = _source("aos/api/calls/call.py")
        initiate = call.split("def initiate_call_impl", 1)[1].split("# MARK CALL RINGING", 1)[0]
        self.assertIn("enqueue_room_provisioning(call.name)", initiate)
        self.assertNotIn("notify_incoming_call", initiate)
        self.assertNotIn("publish_incoming_call", initiate)
        task = _source("aos/tasks/calls.py")
        provision = task.split("def provision_call_room", 1)[1].split("def _mark_call_as_missed", 1)[0]
        self.assertIn("frappe.db.commit()", provision)
        self.assertLess(provision.index("frappe.db.commit()"), provision.index("provision_livekit_room"))
        self.assertIn("incoming_dispatched_at", provision)
        self.assertIn("ring_expires_at", provision)
        self.assertIn('content="📞 Calling..."', provision)
        self.assertNotIn('content="📞 Calling..."', initiate)
        self.assertIn("notify_incoming_call", provision)
        self.assertIn("publish_call_ready", provision)
        token = _source("aos/api/calls/token.py")
        self.assertIn("ensure_call_join_ready(call)", token)
        self.assertIn("ring_expires_at", token)
        status = _source("aos/api/calls/status.py")
        self.assertIn("ring_expires_at", status)
        delivery = _source("aos/services/notifications/delivery.py")
        self.assertIn('"ring_expires_at"', delivery)

        # Terminal lifecycle state wins over RTC readiness. A late accept after
        # cancel/reject/miss must return INVALID_STATE rather than CALL_NOT_READY.
        accept = call.split("def accept_call_impl", 1)[1].split("# REJECT CALL", 1)[0]
        state_check = accept.index("validate_can_accept(call)")
        readiness_check = accept.index("ensure_call_join_ready(call)", state_check)
        self.assertLess(state_check, readiness_check)

    def test_call_tokens_are_source_scoped_without_changing_live_grants(self):
        service = _source("aos/services/livekit_service.py")
        call_block = service.split("def generate_call_token", 1)[1].split("# LIVE TOKENS", 1)[0]
        self.assertIn('["microphone"]', call_block)
        self.assertIn('publish_sources.append("camera")', call_block)
        self.assertIn("can_publish_data=False", call_block)
        self.assertIn("can_update_own_metadata=False", call_block)
        live_block = service.split("def generate_live_token", 1)[1].split("def normalize_live_role", 1)[0]
        self.assertIn("can_publish_sources=None", live_block)
        self.assertIn("can_update_own_metadata=None", live_block)
        admin = _source("aos/services/livekit/admin.py")
        self.assertIn("max_participants: int | None = None", admin)
        calls_livekit = _source("aos/services/calls/livekit.py")
        self.assertIn("CALL_ROOM_MAX_PARTICIPANTS = 2", calls_livekit)
        self.assertIn("deduplicate=True", calls_livekit)

    def test_missed_calls_do_not_consume_sleeping_workers(self):
        tasks = _source("aos/tasks/calls.py")
        self.assertNotIn("time.sleep", tasks)
        self.assertNotIn("def handle_call_timeout", tasks)
        self.assertIn("incoming_dispatched_at", tasks)
        self.assertIn("MISSED_CALL_BATCH_SIZE", tasks)
        constants = _source("aos/api/calls/constants.py")
        self.assertNotIn("CALL_TIMEOUT_JOB_PATH", constants)

    def test_initiation_is_participant_locked_globally_and_retry_idempotent(self):
        source = _source("aos/api/calls/call.py")
        block = source.split("def initiate_call_impl", 1)[1].split("# MARK CALL RINGING", 1)[0]
        self.assertIn("lock_users_for_call(current_user, receiver)", block)
        self.assertIn("active_call_for_users(current_user, receiver)", block)
        self.assertIn("Call already active.", block)
        self.assertIn("INCOMING_CALL_LIMIT_PER_MINUTE_PER_TARGET", block)
        self.assertIn("ensure_interaction_allowed", block)

    def test_active_call_mutations_recheck_canonical_accounts_and_social_policy(self):
        policy = _source("aos/services/calls/policy.py")
        self.assertIn("ensure_account_active", policy)
        self.assertIn("ensure_not_blocked", policy)
        self.assertIn("AOS Profile", policy)
        for relative in ("aos/api/calls/call.py", "aos/api/calls/token.py", "aos/api/calls/status.py"):
            self.assertIn("ensure_call_interaction_allowed", _source(relative), relative)

    def test_incoming_delivery_stays_transient_and_only_missed_is_persistent(self):
        source = _source("aos/services/notifications/service.py")
        incoming = source.split("def notify_incoming_call", 1)[1].split("def notify_missed_call", 1)[0]
        missed = source.split("def notify_missed_call", 1)[1].split("# FOLLOW", 1)[0]
        self.assertIn("deliver_transient", incoming)
        self.assertNotIn("cls.notify(", incoming)
        self.assertIn('type="missed_call"', missed)
        self.assertIn("dedupe_key=f\"call:missed:", missed)
        self.assertIn("public_account_id_for_user", incoming)
        self.assertIn("public_account_id_for_user", missed)

    def test_android_incoming_push_is_data_only_without_changing_normal_notifications(self):
        source = _source("infra/notification-delivery/app/worker.py")
        self.assertIn("_is_transient_incoming_call", source)
        self.assertIn('"android_data_only"', source)
        self.assertIn("if data_only", source)
        self.assertIn("collapse_key=_incoming_call_collapse_key", source)
        self.assertIn("include_notification_options=not data_only", source)
        self.assertIn("messaging.Notification", source)
        self.assertIn("iOS/web retain the existing", source)

    def test_calls_metrics_are_exposed_without_sensitive_labels(self):
        metrics = _source("aos/utils/metrics.py")
        self.assertIn("def record_call_event", metrics)
        self.assertIn("_safe_call_metrics", metrics)
        self.assertIn("call_events", metrics)
        observability = _source("aos/services/calls/observability.py")
        self.assertIn("record_call_event", observability)

    def test_livekit_room_cleanup_is_durable_and_reconciled(self):
        livekit = _source("aos/services/calls/livekit.py")
        self.assertIn("room_cleanup_pending", livekit)
        self.assertIn("enqueue_after_commit=True", livekit)
        self.assertIn("ROOM_CLEANUP_BATCH_SIZE = 20", livekit)
        tasks = _source("aos/tasks/calls.py")
        self.assertIn("def cleanup_call_room", tasks)
        self.assertIn("def reconcile_call_rooms", tasks)
        hooks = _source("aos/hooks.py")
        self.assertIn("aos.tasks.calls.reconcile_call_rooms", hooks)

    def test_ongoing_reconciliation_is_conservative_and_reconnect_safe(self):
        source = _source("aos/services/calls/reconciliation.py")
        self.assertIn("ROOM_MISSING_CONFIRM_SECONDS = 300", source)
        self.assertIn("list_participants", source)
        self.assertIn("rtc_missing_since", source)
        self.assertIn("status = 'failed'", source)
        self.assertIn("ensure_call_interaction_allowed", source)
        self.assertIn("def clear_missing_room_marker", source)
        hooks = _source("aos/hooks.py")
        self.assertIn("aos.tasks.calls.reconcile_active_call_state", hooks)

    def test_history_queries_and_mutations_are_bounded_and_batch_profiles(self):
        source = _source("aos/api/calls/history.py")
        self.assertIn("CLEAR_CALL_HISTORY_BATCH_SIZE = 500", source)
        self.assertIn("MAX_GROUP_DETAIL_ROWS = 5000", source)
        self.assertIn("CALL_INPUT_TOO_LARGE", source)
        self.assertIn("get_user_display_map", source)
        self.assertNotIn("frappe.db.commit(", source)
        cursor = source.split("def _resolve_history_cursor", 1)[1].split("# LIST CALLS", 1)[0]
        self.assertIn("return call.creation, call.name, None", cursor)

    def test_call_rate_limits_use_shared_safe_keys_and_registry_is_complete(self):
        for path in (ROOT / "aos/api/calls").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "rate_limit(" in source:
                self.assertIn("rate_limit_key", source, str(path.relative_to(ROOT)))
                self.assertNotRegex(source, r'key=f["\']aos:calls:')
        entries = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        registry = {entry["endpoint"]: entry for entry in entries}
        for endpoint in (
            "initiate_call", "mark_call_ringing", "accept_call", "reject_call", "cancel_call", "end_call",
            "request_video_upgrade", "respond_video_upgrade", "get_call_status", "get_call_token", "list_calls",
            "get_call_group_details", "delete_call_logs", "clear_call_history",
        ):
            key = f"aos.api.v1.calls.__init__.{endpoint}"
            self.assertIn(key, registry)
            self.assertEqual(registry[key]["access"], "authenticated")

    def test_calls_logs_do_not_capture_raw_tracebacks_or_tokens(self):
        offenders = []
        for root in CALL_SCOPE:
            for path in root.rglob("*.py"):
                if "tests" in path.parts:
                    continue
                source = path.read_text(encoding="utf-8")
                if "frappe.get_traceback()" in source:
                    offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(offenders, [])
        observability = _source("aos/services/calls/observability.py")
        for private in ("token", "session", "push_token", "api_secret", "password", "email"):
            self.assertNotIn(f'"{private}"', observability)

    def test_calls_migrations_are_ordered_bounded_and_side_effect_free(self):
        patches = _source("aos/patches.txt")
        data = patches.index("aos.patches.v1_0.harden_calls_subsystem")
        indexes = patches.index("aos.patches.v1_0.install_call_indexes")
        self.assertLess(data, indexes)
        migration = _source("aos/patches/v1_0/harden_calls_subsystem.py")
        self.assertIn("BATCH = 100", migration)
        self.assertIn("_resolve_overlapping_active_calls", migration)
        self.assertLess(
            migration.index("_normalize_active_flags()"),
            migration.index("_resolve_overlapping_active_calls()"),
        )
        self.assertNotIn("frappe.db.commit", migration)
        self.assertNotIn("frappe.enqueue", migration)
        self.assertNotIn("delete_room", migration)
        public_data = patches.index("aos.patches.v1_0.harden_calls_public_contract")
        public_indexes = patches.index("aos.patches.v1_0.install_call_public_indexes")
        timeout_cleanup = patches.index("aos.patches.v1_0.remove_legacy_call_timeout_index")
        self.assertLess(indexes, public_data)
        self.assertLess(public_data, public_indexes)
        self.assertLess(public_indexes, timeout_cleanup)
        public_migration = _source("aos/patches/v1_0/harden_calls_public_contract.py")
        self.assertIn("_backfill_public_ids", public_migration)
        self.assertIn("_fail_legacy_active_calls", public_migration)
        self.assertNotIn("frappe.db.commit", public_migration)
        self.assertNotIn("frappe.enqueue", public_migration)
        public_installer = _source("aos/patches/v1_0/install_call_public_indexes.py")
        self.assertIn("uq_call_public_id", public_installer)
        self.assertIn("idx_call_ring_expiry", public_installer)
        self.assertIn("ring_expires_at", public_installer)
        self.assertIn("idx_call_provision_recovery", public_installer)
        self.assertIn("DROP INDEX `idx_call_timeout`", public_installer)
        installer = _source("aos/patches/v1_0/install_call_indexes.py")
        self.assertIn("uq_call_room_name", installer)
        self.assertIn("idx_call_caller_active", installer)
        self.assertIn("idx_call_receiver_active", installer)
        self.assertIn("idx_call_room_cleanup", installer)
        # The post-migrate invariant is canonical and must never recreate the
        # deprecated ringing_at timeout index. The forward cleanup patch handles
        # sites where an earlier migration already recorded the first cleanup.
        self.assertNotIn('"idx_call_timeout"', installer)
        cleanup = _source("aos/patches/v1_0/remove_legacy_call_timeout_index.py")
        self.assertIn("DROP INDEX `idx_call_timeout`", cleanup)
        self.assertNotIn("frappe.db.commit", cleanup)
        self.assertNotIn("frappe.enqueue", cleanup)
        self.assertNotIn("delete_room", cleanup)
        self.assertNotIn("frappe.db.commit", installer)
        after_migrate = _source("aos/migrate.py")
        self.assertIn("install_call_indexes.execute", after_migrate)
        self.assertIn("install_call_public_indexes.execute", after_migrate)
        self.assertLess(
            after_migrate.index("install_call_indexes.execute"),
            after_migrate.index("install_call_public_indexes.execute"),
        )

    def test_account_deletion_ends_calls_and_defers_room_cleanup(self):
        source = _source("aos/services/account_deletion_service.py")
        block = source.split("def _end_active_calls", 1)[1].split("def ", 1)[0]
        self.assertIn("room_cleanup_pending", block)
        self.assertIn("enqueue_room_cleanup", block)
        self.assertNotIn("delete_room(", block)

    def test_only_existing_call_states_and_types_are_preserved(self):
        import json as _json
        doc = _json.loads(_source("aos/aos/doctype/aos_call/aos_call.json"))
        fields = {field["fieldname"]: field for field in doc["fields"]}
        self.assertEqual(
            fields["status"]["options"].splitlines(),
            ["initiated", "ringing", "ongoing", "cancelled", "rejected", "missed", "ended", "failed"],
        )
        self.assertEqual(fields["call_type"]["options"].splitlines(), ["audio", "video"])
        self.assertNotIn("participant", fields)

    def test_calls_do_not_claim_a_livekit_webhook_the_repo_does_not_model(self):
        calls_files = "\n".join(
            path.read_text(encoding="utf-8")
            for root in CALL_SCOPE for path in root.rglob("*.py") if "tests" not in path.parts
        )
        self.assertNotIn("WebhookReceiver", calls_files)
        self.assertNotIn("verify_webhook", calls_files)
        global_webhook = _source("aos/api/v1/livekit/__init__.py")
        self.assertIn("live", global_webhook.lower())

    def test_required_calls_documents_exist(self):
        for name in ("README.md", "api.md", "realtime.md", "livekit.md", "operations.md", "testing.md"):
            self.assertTrue((ROOT / "docs/features/calls" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
