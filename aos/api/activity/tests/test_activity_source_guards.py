from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class TestActivityProductionSourceGuards(unittest.TestCase):
    def test_public_surface_preserves_exact_existing_endpoints(self):
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

    def test_public_endpoints_are_private_rate_limited_and_transaction_safe(self):
        source = _source("aos/api/activity/activity.py")
        self.assertEqual(source.count("require_login()"), 3)
        self.assertEqual(source.count("set_private_no_store()"), 3)
        self.assertIn("LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER", source)
        self.assertIn("HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER", source)
        self.assertIn("CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER", source)
        self.assertIn("frappe.db.savepoint(savepoint)", source)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", source)
        self.assertNotIn("frappe.db.commit", source)
        self.assertNotIn("frappe.db.rollback()", source)

    def test_public_activity_errors_use_stable_safe_messages(self):
        source = _source("aos/api/activity/activity.py")
        self.assertIn("_PUBLIC_ACTIVITY_ERROR_MESSAGES", source)
        self.assertIn('"VALIDATION_ERROR": "Invalid activity request."', source)
        self.assertIn('"NOT_FOUND": "Activity not found."', source)
        self.assertNotIn("fail(exc.message", source)
        self.assertNotIn("str(exc)", source)

    def test_request_validation_is_strict_alias_aware_and_bounded(self):
        source = _source("aos/services/activity/validation.py")
        constants = _source("aos/services/activity/constants.py")
        self.assertIn("Unsupported activity fields", source)
        self.assertIn("Conflicting", source)
        self.assertIn("MAX_ACTIVITY_LIMIT", source)
        self.assertIn("MAX_ACTIVITY_START", source)
        self.assertIn("VALID_ACTIVITY_GROUPS", source)
        self.assertIn("VALID_ACTIVITY_TYPES", source)
        self.assertIn('LIST_FIELDS = frozenset', constants)

    def test_activity_types_are_only_repository_supported_types(self):
        constants = _source("aos/services/activity/constants.py")
        expected = {
            "ad_view", "ad_wishlist", "ad_posted", "ad_report",
            "short_watch", "short_like", "short_comment", "short_repost", "short_report",
            "user_search", "user_follow", "user_block", "user_report",
            "live_host", "live_join", "live_comment",
        }
        namespace: dict[str, object] = {}
        exec(compile(constants, "constants.py", "exec"), namespace)
        self.assertEqual(set(namespace["VALID_ACTIVITY_TYPES"]), expected)

    def test_active_duplicate_integrity_is_database_backed(self):
        schema = json.loads(_source("aos/aos/doctype/aos_user_activity/aos_user_activity.json"))
        fields = {row.get("fieldname"): row for row in schema["fields"]}
        self.assertIn("active_key", fields)
        self.assertTrue(fields["active_key"].get("hidden"))
        service = _source("aos/services/activity_service.py")
        self.assertIn("active_key_for", service)
        self.assertIn("FOR UPDATE", service)
        self.assertIn("is_duplicate_entry_error", service)
        self.assertIn('"creation <= %s"', service)
        indexes = _source("aos/patches/v1_0/install_activity_indexes.py")
        self.assertIn("uq_aos_activity_active", indexes)

    def test_serializer_never_returns_raw_internal_doctype_or_unbounded_metadata(self):
        source = _source("aos/services/activity/serializers.py")
        self.assertIn("PUBLIC_TARGET_KIND_BY_ROUTE", source)
        self.assertIn("_METADATA_ALLOWLIST", source)
        self.assertNotIn('"seller_user"', source.split("_METADATA_ALLOWLIST", 1)[1].split("def _value", 1)[0])
        for forbidden in ("session_id", "view_id", "report_id"):
            self.assertNotIn(f'"{forbidden}"', source.split("_METADATA_ALLOWLIST", 1)[1].split("def _value", 1)[0])
        self.assertIn("public_target_name = route_id", source)
        self.assertIn("normalize_public_account_id", source)
        self.assertIn("normalize_public_seller_id", source)
        self.assertIn('{"short_owner", "host_user", "target_user"}', source)

    def test_feature_hooks_store_public_identity_and_short_hooks_are_best_effort(self):
        ads = _source("aos/api/ads/activity.py")
        shorts = _source("aos/api/shorts/activity.py")
        social = _source("aos/api/social/activity.py")
        live = _source("aos/api/live/activity.py")
        self.assertIn("public_seller_id_for_name", ads)
        self.assertIn("public_account_id_for_user(short.owner)", shorts)
        self.assertIn('"target_name": public_user', social)
        self.assertIn("def _safe_record", shorts)
        self.assertNotIn('metadata["session_id"]', live)
        self.assertNotIn('metadata["view_id"]', live)

    def test_activity_is_server_owned_and_not_shareable_or_web_indexed(self):
        schema = json.loads(_source("aos/aos/doctype/aos_user_activity/aos_user_activity.json"))
        self.assertFalse(bool(schema.get("allow_rename")))
        self.assertFalse(bool(schema.get("index_web_pages_for_search")))
        self.assertFalse(bool(schema.get("track_changes")))
        manager = next(row for row in schema["permissions"] if row.get("role") == "System Manager")
        for key in ("create", "write", "delete", "share", "email", "export", "print"):
            self.assertFalse(bool(manager.get(key)), key)
        self.assertTrue(bool(manager.get("read")))

    def test_account_deletion_removes_private_activity_and_redacts_deleted_profile_snapshots(self):
        source = _source("aos/services/account_deletion_service.py")
        block = source.split("def _cleanup_activity_account_data", 1)[1].split("def _remove_social_graph", 1)[0]
        self.assertIn('"AOS User Activity"', block)
        self.assertIn("activity_rows_removed", block)
        self.assertIn("activity_profile_rows_redacted", block)
        self.assertIn("activity_content_rows_redacted", block)
        self.assertIn("Unavailable content", block)
        self.assertIn("active_key = NULL", block)
        self.assertIn("metadata_json = '{{}}'", block)

    def test_migrations_are_registered_data_then_schema_and_do_not_commit(self):
        patches = _source("aos/patches.txt")
        data_patch = _source("aos/patches/v1_0/harden_activity_subsystem.py")
        index_patch = _source("aos/patches/v1_0/install_activity_indexes.py")
        data_pos = patches.index("aos.patches.v1_0.harden_activity_subsystem")
        index_pos = patches.index("aos.patches.v1_0.install_activity_indexes")
        self.assertLess(data_pos, index_pos)
        self.assertIn("HAVING COUNT(*) > 1", data_patch)
        self.assertNotIn("frappe.reload_doc", data_patch)
        self.assertNotIn("frappe.db.commit", data_patch)
        self.assertIn("idx_aos_activity_user_timeline", index_patch)
        self.assertIn("idx_aos_activity_route_target", index_patch)
        self.assertIn("public_account_id_for_user", data_patch)
        self.assertIn("public_seller_id_for_name", data_patch)

    def test_observability_does_not_log_private_activity_payloads(self):
        source = _source("aos/services/activity/observability.py")
        for forbidden in ("user", "email", "query", "metadata", "target_title", "session_id", "route_id"):
            self.assertNotIn(f'"{forbidden}"', source)
        self.assertIn("activity_id", source)

    def test_rate_limit_registry_covers_every_activity_endpoint(self):
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
