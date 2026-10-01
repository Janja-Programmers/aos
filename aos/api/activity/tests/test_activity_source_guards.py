from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestActivityProductionSourceGuards(unittest.TestCase):
    def test_public_surface_is_exactly_list_hide_clear(self):
        source = _source("aos/api/v1/activity/__init__.py")
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
        self.assertEqual(functions, {"list_activity", "hide_activity", "clear_activity"})
        self.assertEqual(source.count("client_kwargs(kwargs)"), 3)
        self.assertNotIn("create_activity", source)

    def test_public_endpoints_are_private_rate_limited_and_do_not_commit(self):
        source = _source("aos/api/activity/activity.py")
        self.assertEqual(source.count("require_login()"), 3)
        self.assertEqual(source.count("set_private_no_store()"), 3)
        for marker in (
            "LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER",
            "HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER",
            "CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER",
            "validate_filter_pair",
            "frappe.db.savepoint(savepoint)",
            "frappe.db.rollback(save_point=savepoint)",
            'rate_limit_key("activity"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("frappe.db.commit", source)

    def test_request_contract_is_strict_current_only(self):
        constants = _source("aos/services/activity/constants.py")
        validation = _source("aos/services/activity/validation.py")
        self.assertIn('LIST_FIELDS = frozenset({"limit", "cursor", "group", "type"})', constants)
        self.assertIn('HIDE_FIELDS = frozenset({"activity_id"})', constants)
        self.assertIn('CLEAR_FIELDS = frozenset({"group", "type"})', constants)
        self.assertIn("Unsupported activity fields", validation)
        self.assertIn("Activity type does not belong to the selected group", validation)
        self.assertIn("MAX_ACTIVITY_LIMIT", validation)
        for forbidden_field in ("MAX_ACTIVITY_START", 'activity_group"', 'activity_type"', 'start"'):
            self.assertNotIn(forbidden_field, validation)

    def test_taxonomy_contains_only_real_current_producers(self):
        constants = _source("aos/services/activity/constants.py")
        tree = ast.parse(constants)
        event_types: set[str] | None = None
        for node in tree.body:
            value = None
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "EVENT_SPECS" for t in node.targets):
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "EVENT_SPECS":
                value = node.value
            if isinstance(value, ast.Dict):
                event_types = {str(key.value) for key in value.keys if isinstance(key, ast.Constant)}
                break
        self.assertEqual(
            event_types,
            {
                "ad_view", "ad_wishlist", "ad_posted", "ad_report", "short_report",
                "user_search", "user_follow", "user_block", "user_report",
                "live_host", "live_join", "live_comment",
            },
        )
        for removed in ("short_watch", "short_like", "short_comment", "short_repost"):
            self.assertNotIn(removed, event_types or set())

    def test_metadata_is_typed_bounded_and_not_arbitrary_json(self):
        service = _source("aos/services/activity_service.py")
        constants = _source("aos/services/activity/constants.py")
        for marker in (
            'schema = dict(spec.get("metadata") or {})',
            'required = frozenset(spec.get("required_metadata") or ())',
            "Unsupported Activity metadata field",
            "Invalid Activity account identity",
            "Invalid Activity seller identity",
            "METADATA_JSON_MAX_BYTES",
            "METADATA_MAX_KEYS",
            "METADATA_TEXT_MAX_LEN",
        ):
            self.assertIn(marker, service)
        for kind in ('"account_id"', '"seller_id"', '"number"', '"bool"', '"int"'):
            self.assertIn(kind, constants)

    def test_database_backed_public_id_and_two_dedupe_modes(self):
        schema = json.loads(_source("aos/aos/doctype/aos_user_activity/aos_user_activity.json"))
        fields = {row.get("fieldname"): row for row in schema["fields"]}
        self.assertTrue(fields["public_id"].get("unique"))
        self.assertTrue(fields["active_key"].get("hidden"))
        self.assertTrue(fields["event_key"].get("hidden"))
        doctype = _source("aos/aos/doctype/aos_user_activity/aos_user_activity.py")
        self.assertIn("self.public_id = new_activity_id()", doctype)
        service = _source("aos/services/activity_service.py")
        for marker in (
            "normalize_unique_key", "active_key_for", "event_key_for", "_find_active_by_key", "_lock_active_by_key",
            "is_duplicate_entry_error", "record_or_update_activity", "record_activity",
        ):
            self.assertIn(marker, service)
        indexes = _source("aos/patches/v1_0/install_activity_indexes.py")
        self.assertIn("uq_aos_activity_active", indexes)
        self.assertIn("uq_aos_activity_event", indexes)

    def test_listing_is_keyset_bounded_and_has_no_count_or_offset(self):
        source = _source("aos/api/activity/activity.py")
        for marker in (
            "decode_cursor", "encode_cursor", "last_occurrence_at < %(cursor_last)s",
            "public_id < %(cursor_id)s", "ORDER BY last_occurrence_at DESC, creation DESC, public_id DESC",
            '"limit": limit + 1',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("frappe.db.count", source)
        self.assertNotIn("OFFSET", source.upper())
        cursor = _source("aos/services/activity/cursor.py")
        self.assertIn("_scope", cursor)
        self.assertIn("normalize_activity_id", cursor)
        self.assertIn("MAX_CURSOR_LENGTH", cursor)
        self.assertIn("validate=True", cursor)

    def test_serializer_exposes_public_semantics_not_storage_identity(self):
        source = _source("aos/services/activity/serializers.py")
        self.assertIn('"id": str(_value(row, "public_id")', source)
        self.assertIn("PUBLIC_TARGET_KIND_BY_ROUTE", source)
        public_return = source.split("return {", 1)[1]
        for forbidden in ('"target_doctype"', '"target_name"', '"unique_key"', '"active_key"', '"event_key"'):
            self.assertNotIn(forbidden, public_return)
        self.assertIn('"id": route_id', source)

    def test_projection_consumes_hardened_domain_boundaries_in_batches(self):
        source = _source("aos/services/activity/projection.py")
        for marker in (
            "public_ad_sql_context", "serialize_internal_identity_map", "SocialCapabilityService",
            "filter_viewable_rows", "resource_available", "AOS Live Message",
        ):
            self.assertIn(marker, source)
        self.assertIn('filters={"name": ["in",', source)
        self.assertIn("has_blocked_me", source)
        self.assertNotIn('str(live.get("status") or "") == "live"', source)
        self.assertIn("canonical get-live read contract permits ended streams", source)

    def test_producer_hooks_are_savepoint_isolated_and_no_fanout(self):
        helper = _source("aos/services/activity/producer.py")
        self.assertIn("frappe.db.savepoint(savepoint)", helper)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", helper)
        self.assertNotIn("frappe.db.commit", helper)
        for path in (
            "aos/api/ads/activity.py", "aos/api/social/activity.py",
            "aos/api/live/activity.py", "aos/services/shorts/activity.py",
        ):
            source = _source(path)
            self.assertIn("best_effort_activity", source, path)
            self.assertNotIn('"doctype": "AOS User Activity"', source, path)
            self.assertNotIn("followers", source.lower(), path)

    def test_producers_do_not_recreate_analytics_or_other_domain_state(self):
        shorts = _source("aos/services/shorts/activity.py")
        live = _source("aos/api/live/activity.py")
        ads = _source("aos/api/ads/activity.py")
        social = _source("aos/api/social/activity.py")
        self.assertIn("def record_short_report_activity", shorts)
        for dead in ("record_short_watch_activity", "record_short_like_activity", "record_short_comment_activity", "record_short_repost_activity"):
            self.assertNotIn(dead, shorts)
        for analytics_key in ('"viewer_count"', '"session_id"', '"view_id"', '"live_status"'):
            self.assertNotIn(analytics_key, live)
        self.assertIn('"route_id": ad.public_id', ads)
        self.assertIn('"target_name": target_user', social)
        self.assertIn('"route_id": public_user', social)

    def test_no_activity_calls_chat_reviews_notifications_or_calls_state_machine(self):
        activity_sources = "\n".join(
            _source(path)
            for path in (
                "aos/services/activity_service.py", "aos/services/activity/projection.py",
                "aos/services/activity/serializers.py", "aos/api/activity/activity.py",
            )
        )
        for forbidden in ("AOS Call", "AOS Conversation", "AOS Message", "AOS Notification", "AOS Review"):
            self.assertNotIn(forbidden, activity_sources)
        self.assertNotIn("NotificationService", activity_sources)

    def test_clear_retention_and_query_work_are_bounded(self):
        constants = _source("aos/services/activity/constants.py")
        service = _source("aos/services/activity_service.py")
        task = _source("aos/tasks/activity.py")
        hooks = _source("aos/hooks.py")
        indexes = _source("aos/patches/v1_0/install_activity_indexes.py")
        self.assertIn("CLEAR_BATCH_SIZE = 500", constants)
        self.assertIn("RETENTION_DELETE_BATCH_SIZE = 5_000", constants)
        self.assertIn("ACTIVITY_COUNT_MAX = 2_147_483_647", constants)
        self.assertIn("LEAST(GREATEST(COALESCE(`count`, 0), 1) + 1", service)
        self.assertIn("ACTIVITY_RETENTION_DAYS = 180", constants)
        self.assertIn("CLEAR_BATCH_SIZE + 1", service)
        self.assertIn("WHERE last_occurrence_at < %s", service)
        self.assertIn("idx_aos_activity_retention", indexes)
        self.assertIn("_MAX_BATCHES_PER_RUN = 10", task)
        self.assertIn("aos.tasks.activity.cleanup_activity_retention", hooks)
        self.assertNotIn("frappe.db.commit", task)

    def test_account_lifecycle_is_recoverable_then_permanent_purge(self):
        recoverable = _source("aos/services/account_deletion_service.py")
        tombstone = recoverable.split("def tombstone_deleted_account_features", 1)[1].split("def restore_deleted_account_features", 1)[0]
        self.assertNotIn("AOS User Activity", tombstone)
        purge = _source("aos/services/account_purge_service.py")
        self.assertIn("def _purge_activity_private_batch", purge)
        self.assertIn("SELECT public_id FROM `tabAOS Ad`", purge)
        self.assertIn("active_key = NULL, event_key = NULL", purge)
        self.assertIn("target_doctype = '', target_name = ''", purge)
        self.assertIn("route_type = '', route_id = ''", purge)
        self.assertIn("metadata_json = '{}'", purge)

    def test_activity_fresh_schema_has_direct_index_installer(self):
        patches = _source("aos/patches.txt")
        self.assertIn("aos.patches.v1_0.install_activity_indexes", patches)
        self.assertIn("install_activity_indexes.execute", _source("aos/migrate.py"))

    def test_only_one_authoritative_activity_document_exists(self):
        docs = sorted((ROOT / "docs/features/activity").glob("*.md"))
        self.assertEqual([path.name for path in docs], ["README.md"])
        readme = docs[0].read_text(encoding="utf-8")
        for heading in (
            "## Overview", "## Ownership", "## Producers", "## Event Taxonomy", "## Data Model",
            "## Naming", "## API", "## Visibility / Privacy", "## Resource Lifecycle",
            "## Account Lifecycle", "## Transactions / Idempotency", "## Fan-out / Write Model",
            "## Pagination", "## Caching / Realtime", "## Cross-feature Dependencies", "## Testing",
        ):
            self.assertIn(heading, readme)

    def test_server_owned_desk_permissions_and_safe_observability(self):
        schema = json.loads(_source("aos/aos/doctype/aos_user_activity/aos_user_activity.json"))
        manager = next(row for row in schema["permissions"] if row.get("role") == "System Manager")
        for key in ("create", "write", "delete", "share", "email", "export", "print"):
            self.assertFalse(bool(manager.get(key)), key)
        self.assertTrue(bool(manager.get("read")))
        logging = _source("aos/services/activity/observability.py")
        for forbidden in ("user", "email", "query", "metadata", "target_title", "session_id", "route_id"):
            self.assertNotIn(f'"{forbidden}"', logging)

    def test_rate_limit_registry_covers_all_three_public_endpoints(self):
        registry = json.loads(_source("ci/public-endpoint-rate-limits.json"))
        endpoints = {row["endpoint"] for row in registry}
        self.assertTrue(
            {
                "aos.api.v1.activity.__init__.list_activity",
                "aos.api.v1.activity.__init__.hide_activity",
                "aos.api.v1.activity.__init__.clear_activity",
            }.issubset(endpoints)
        )


if __name__ == "__main__":
    unittest.main()
