from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
LIVE_SCOPE = (
    ROOT / "aos/api/live",
    ROOT / "aos/api/v1/live",
    ROOT / "aos/services/live",
)


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


class TestLiveSourceGuards(unittest.TestCase):
    def test_public_v1_surface_is_complete_and_thin(self):
        source = _source("aos/api/v1/live/__init__.py")
        endpoints = re.findall(r"^def ([a-z_]+)\(\*\*kwargs\):", source, flags=re.MULTILINE)
        self.assertEqual(len(endpoints), 24)
        self.assertEqual(len(endpoints), len(set(endpoints)))
        self.assertIn("_client_kwargs(kwargs)", source)
        self.assertIn("run_live_api(", source)

    def test_no_internal_commit_in_live_domain(self):
        violations: list[str] = []
        paths = []
        for root in LIVE_SCOPE:
            paths.extend(root.rglob("*.py"))
        paths.extend(
            [
                ROOT / "aos/tasks/live.py",
                ROOT / "aos/patches/v1_0/harden_live_subsystem.py",
                ROOT / "aos/patches/v1_0/install_live_indexes.py",
            ]
        )
        for path in paths:
            if "tests" in path.parts:
                continue
            if "frappe.db.commit(" in path.read_text(encoding="utf-8"):
                violations.append(str(path.relative_to(ROOT)))
        self.assertEqual(violations, [])

    def test_external_room_calls_are_background_isolated(self):
        admin = _source("aos/services/live/livekit_admin.py")
        tasks = _source("aos/tasks/live.py")
        live_api = _source("aos/api/live/live.py")
        self.assertIn("asyncio.wait_for", admin)
        self.assertIn("LIVEKIT_ADMIN_RETRY_ATTEMPTS", admin)
        self.assertIn("remove_participant", admin)
        self.assertIn("enqueue_after_commit=True", live_api)
        self.assertIn("cleanup_live_room", tasks)
        self.assertIn("reconcile_live_state", tasks)

    def test_livekit_identity_and_grants_do_not_embed_private_user_values(self):
        identity = _source("aos/services/live/livekit.py")
        service = _source("aos/services/livekit_service.py")
        self.assertIn("hmac.new", identity)
        self.assertNotIn("email", identity.lower())
        self.assertNotIn('f"aos:{role}:{user}', identity)
        viewer_block = service.split("LIVE_ROLE_VIEWER:", 1)[1].split("},", 1)[0]
        self.assertIn('"can_publish": False', viewer_block)
        self.assertIn('"can_publish_data": False', viewer_block)

    def test_host_cohost_invite_resolves_opaque_identity_server_side(self):
        endpoints = _source("aos/services/live/endpoints.py")
        cohost = _source("aos/api/live/cohost.py")
        self.assertIn('"livekit_identity"', endpoints)
        self.assertIn("LIVEKIT_PARTICIPANT_ID_RE", endpoints)
        self.assertIn("_get_active_invite_candidate_by_identity", cohost)
        self.assertIn("_lock_and_revalidate_invite_candidate_view", cohost)
        self.assertIn("FOR UPDATE", cohost)
        self.assertIn('host_payload["livekit_identity"]', cohost)
        self.assertIn("candidate_private_payload", cohost)
        invite = cohost.split("def invite_live_cohost_impl", 1)[1].split(
            "# VIEWER REQUESTS CO-HOSTING", 1
        )[0]
        self.assertNotIn('host_payload["session_id"]', invite)

    def test_webhook_is_verified_and_replay_safe(self):
        source = _source("aos/services/live/webhooks.py")
        self.assertIn("WebhookReceiver", source)
        self.assertIn("TokenVerifier", source)
        self.assertIn("event_id", source)
        self.assertIn("is_duplicate_entry_error", source)
        self.assertNotIn("raw_body=", source)

    def test_realtime_publications_are_after_commit(self):
        source = _source("aos/api/live/realtime.py")
        marker = "frappe.publish_realtime("
        starts = [m.start() for m in re.finditer(re.escape(marker), source)]
        self.assertGreater(len(starts), 0)
        for start in starts:
            snippet = source[start : start + 400]
            self.assertIn("after_commit=True", snippet)

    def test_reconciliation_patch_is_bounded_and_has_no_external_calls(self):
        source = _source("aos/patches/v1_0/harden_live_subsystem.py")
        self.assertIn("def _bounded_update", source)
        self.assertIn("LIMIT %(batch)s", source)
        self.assertIn("def _normalize_duplicate_room_names", source)
        self.assertIn("GROUP BY room_name HAVING COUNT(*) > 1", source)
        self.assertNotIn("LiveKitAPI", source)
        self.assertNotIn("frappe.enqueue", source)

    def test_hardening_patches_run_data_before_unique_indexes(self):
        patches = _source("aos/patches.txt")
        data = patches.index("aos.patches.v1_0.harden_live_subsystem")
        indexes = patches.index("aos.patches.v1_0.install_live_indexes")
        self.assertLess(data, indexes)

    def test_new_uniqueness_boundaries_are_declared(self):
        paths_and_fields = {
            "aos/aos/doctype/aos_live_stream/aos_live_stream.json": "active_host_key",
            "aos/aos/doctype/aos_live_cohost/aos_live_cohost.json": "active_workflow_key",
            "aos/aos/doctype/aos_live_message/aos_live_message.json": "active_idempotency_key",
        }
        for path, fieldname in paths_and_fields.items():
            with self.subTest(path=path):
                payload = json.loads(_source(path))
                field = next(item for item in payload["fields"] if item.get("fieldname") == fieldname)
                self.assertEqual(field.get("unique"), 1)

    def test_live_token_ttl_is_distinct_and_bounded(self):
        constants = _source("aos/services/live/constants.py")
        settings = _source("aos/utils/aos_settings.py")
        self.assertIn("LIVEKIT_LIVE_TOKEN_TTL_DEFAULT_MINUTES = 15", constants)
        self.assertIn("LIVEKIT_LIVE_TOKEN_TTL_MAX_MINUTES = 30", constants)
        self.assertIn("livekit_live_token_ttl_minutes", settings)

    def test_rate_limit_registry_covers_live_surface_and_signed_webhook(self):
        entries = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        registry = {item["endpoint"]: item for item in entries}
        for endpoint in ("start_live", "send_reaction", "invite_live_cohost", "list_live_cohosts"):
            item = registry[f"aos.api.v1.live.__init__.{endpoint}"]
            self.assertIn(item["policy"], {"baseline_authenticated", "baseline_guest"})
        webhook = registry["aos.api.v1.livekit.__init__.handle_webhook"]
        self.assertEqual(webhook["policy"], "signed_internal_exempt")

    def test_transactional_surface_covers_state_and_token_races(self):
        source = _source("aos/services/live/endpoints.py")
        for endpoint in (
            "start_live",
            "join_live",
            "end_live",
            "get_live_token",
            "get_live_cohost_token",
            "track_join",
            "track_leave",
            "add_live_message",
            "reply_live_message",
            "send_reaction",
        ):
            self.assertIn(f'"{endpoint}"', source)

    def test_savepoint_boundary_preserves_outer_transaction_callbacks(self):
        source = _source("aos/services/live/api.py")
        self.assertIn("_snapshot_callbacks", source)
        self.assertIn("_restore_callbacks", source)
        self.assertIn("_restore_outbox_flag", source)
        self.assertIn("rollback(save_point=savepoint)", source)
        self.assertNotIn("frappe.db.rollback()", source)

    def test_webhook_failure_rolls_back_mutation_and_retries(self):
        service = _source("aos/services/live/webhooks.py")
        endpoint = _source("aos/api/v1/livekit/__init__.py")
        self.assertIn("_snapshot_callbacks", service)
        self.assertIn("_rollback(savepoint", service)
        self.assertIn("LIVE_WEBHOOK_RETRY", service)
        self.assertIn("MAX_WEBHOOK_BYTES", endpoint)
        self.assertLess(endpoint.index("content_length"), endpoint.index("get_data("))

    def test_reconciliation_removes_blocked_or_unavailable_connected_viewers(self):
        source = _source("aos/tasks/live.py")
        self.assertIn("tabAOS User Block", source)
        self.assertIn("account_status", source)
        self.assertIn("is_authorized", source)
        self.assertIn("enqueue_view_removal", source)

    def test_account_cleanup_queues_room_participant_removal(self):
        source = _source("aos/services/account_deletion_service.py")
        self.assertIn("enqueue_view_removal", source)
        self.assertIn("enqueue_cohost_removal", source)
        self.assertIn("cleanup_live_room", source)

    def test_live_rate_limit_keys_do_not_embed_raw_private_identifiers(self):
        for relative in (
            "aos/api/live/live.py",
            "aos/api/live/tracking.py",
            "aos/api/live/token.py",
            "aos/api/live/messages.py",
            "aos/api/live/reactions.py",
            "aos/api/live/cohost.py",
        ):
            source = _source(relative)
            self.assertIn("rate_limit_key", source)
            self.assertNotIn("aos:live:", source)

    def test_high_volume_participant_paths_use_shared_lifecycle_locks(self):
        repository = _source("aos/services/live/repository.py")
        social = _source("aos/services/social/repository.py")
        policy = _source("aos/services/live/policy.py")
        self.assertIn("LOCK IN SHARE MODE", repository)
        self.assertIn("def lock_account_pair_shared", social)
        self.assertIn("LOCK IN SHARE MODE", social)
        self.assertIn("lock_account_pair_shared", policy)

        for relative in (
            "aos/api/live/messages.py",
            "aos/api/live/tracking.py",
            "aos/api/live/token.py",
            "aos/api/live/reactions.py",
        ):
            self.assertIn("lock_live_shared", _source(relative), relative)

        live_api = _source("aos/api/live/live.py")
        join = live_api.split("def join_live_impl", 1)[1].split("# END LIVE", 1)[0]
        end = live_api.split("def end_live_impl", 1)[1]
        self.assertIn("lock_live_shared", join)
        self.assertIn("lock_live(live_id)", end)

    def test_shared_live_requests_never_upgrade_to_parent_writes(self):
        tracking = _source("aos/api/live/tracking.py")
        reactions = _source("aos/api/live/reactions.py")
        analytics = _source("aos/services/live_analytics_service.py")

        join = tracking.split("def track_join_impl", 1)[1].split("# TRACK LEAVE", 1)[0]
        self.assertNotIn("sync_view_metrics", join)
        self.assertNotIn('set_value(\n                    "AOS Live Stream"', join)
        self.assertNotIn('set_value(\n                    "AOS Live Stream"', reactions)
        self.assertNotIn("handle_reaction", reactions)

        hot_view = analytics.split("def handle_view_joined", 1)[1].split("def materialize_view_metrics", 1)[0]
        self.assertNotIn("UPDATE `tabAOS Live Stream`", hot_view)
        self.assertNotIn("sync_view_metrics", hot_view)

    def test_hot_live_state_is_redis_backed_and_fanout_is_coalesced(self):
        ephemeral = _source("aos/services/live/ephemeral.py")
        realtime = _source("aos/api/live/realtime.py")
        tasks = _source("aos/tasks/live.py")
        tracking = _source("aos/api/live/tracking.py")

        self.assertIn("_VIEW_JOIN_LUA", ephemeral)
        self.assertIn("increment_view_metrics", ephemeral)
        self.assertIn("increment_reaction", ephemeral)
        self.assertIn("REACTION_PUBLISH_LIMIT_PER_SECOND", ephemeral)
        # Frappe RedisWrapper pickles hash values and only some wrapper methods
        # apply the site prefix. Hot counters must therefore use explicitly
        # namespaced keys plus raw Redis hash/Lua operations consistently.
        self.assertIn("cache.make_key(_reaction_key(live_id))", ephemeral)
        self.assertIn("cache.make_key(_view_key(live_id))", ephemeral)
        self.assertIn("cache.make_key(_comment_key(live_id))", ephemeral)
        self.assertIn('cache.execute_command("HGETALL", key)', ephemeral)
        self.assertNotIn("cache.hgetall(", ephemeral)
        self.assertNotIn("cache.hget(", ephemeral)
        self.assertNotIn("cache.hset(", ephemeral)
        self.assertIn("cache.make_key(_viewer_count_gate_key(live_id))", realtime)
        self.assertIn("publish_coalesced_viewer_count", realtime)
        self.assertIn("materialize_view_metrics", tasks)
        self.assertNotIn("frappe.new_doc(\n        LIVE_MESSAGE_DOCTYPE", tracking.split("def _create_viewer_joined_message", 1)[1].split("# TRACK JOIN", 1)[0])

    def test_live_end_cleanup_is_bounded_and_recoverable(self):
        live_doc = _source("aos/aos/doctype/aos_live_stream/aos_live_stream.py")
        tasks = _source("aos/tasks/live.py")
        self.assertIn("aos.tasks.live.finalize_ended_live_views", live_doc)
        self.assertNotIn("def _close_active_view_sessions", live_doc)
        finalizer = tasks.split("def finalize_ended_live_views", 1)[1].split("def ", 1)[0]
        self.assertIn("LIMIT 500", finalizer)
        self.assertIn("enqueue_after_commit=True", finalizer)
        self.assertIn("pending_terminal_views", tasks)

    def test_livekit_production_config_uses_udp_mux_and_redis(self):
        livekit = _source("infra/livekit/livekit.yaml")
        compose = _source("docker-compose.yml")
        self.assertIn("redis:", livekit)
        self.assertIn("address: livekit-redis:6379", livekit)
        self.assertIn("udp_port: 7882", livekit)
        self.assertNotIn("port_range_start", livekit)
        self.assertNotIn("port_range_end", livekit)
        self.assertIn("livekit-redis:", compose)
        self.assertIn("LIVEKIT_UDP_PORT:-7882", compose)

    def test_notification_deduplication_is_database_enforced(self):
        payload = json.loads(_source("aos/aos/doctype/aos_notification/aos_notification.json"))
        field = next(item for item in payload["fields"] if item.get("fieldname") == "dedupe_key")
        self.assertEqual(field.get("unique"), 1)
        self.assertTrue(field.get("hidden"))
        source = _source("aos/services/live/notifications.py")
        self.assertIn("sha256", source)
        self.assertIn("dedupe_key=", source)

    def test_all_requested_live_documents_exist(self):
        names = (
            "README.md",
            "architecture.md",
            "api.md",
            "lifecycle.md",
            "livekit.md",
            "participants.md",
            "cohosts.md",
            "comments.md",
            "reactions.md",
            "privacy.md",
            "notifications.md",
            "moderation.md",
            "recording.md",
            "migration.md",
            "operations.md",
            "testing.md",
        )
        for name in names:
            self.assertTrue((ROOT / "docs/features/live" / name).is_file(), name)


if __name__ == "__main__":
    unittest.main()
