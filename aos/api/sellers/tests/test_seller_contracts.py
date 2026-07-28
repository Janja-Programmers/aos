from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestSellerContracts(unittest.TestCase):
    def test_v1_wrappers_strip_framework_transport_metadata(self):
        source = (ROOT / "aos/api/v1/sellers/__init__.py").read_text()
        self.assertIn("_client_kwargs(kwargs)", source)
        self.assertNotIn("frappe.db", source)

    def test_core_endpoints_are_thin_service_wrappers(self):
        for filename in ("list_sellers.py", "get_seller.py", "get_my_seller_status.py", "update_my_seller.py"):
            source = (ROOT / "aos/api/sellers" / filename).read_text()
            self.assertIn("SellerService", source)
            self.assertIn("run_seller_api", source)
            self.assertNotIn("frappe.db", source)
            self.assertNotIn("frappe.db.commit", source)

    def test_seller_api_conflicts_use_operation_savepoints(self):
        source = (ROOT / "aos/services/sellers/api.py").read_text()
        self.assertIn("frappe.db.savepoint(savepoint)", source)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", source)
        self.assertIn("_restore_transaction_callbacks", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_read_only_seller_endpoints_validate_before_transaction_sql(self):
        for filename in ("list_sellers.py", "get_seller.py", "get_my_seller_status.py"):
            source = (ROOT / "aos/api/sellers" / filename).read_text()
            self.assertIn("transactional=False", source, filename)
        update_source = (ROOT / "aos/api/sellers/update_my_seller.py").read_text()
        self.assertNotIn("transactional=False", update_source)

    def test_public_seller_identity_is_opaque_and_legacy_names_are_input_only(self):
        identity = (ROOT / "aos/services/sellers/identity.py").read_text()
        serializers = (ROOT / "aos/services/sellers/serializers.py").read_text()
        self.assertIn(r"^SELLER-[A-Z2-7]{20}$", identity)
        self.assertIn("resolve_seller_reference", identity)
        self.assertIn("normalize_public_seller_id", serializers)
        self.assertIn("migration_fallback_public_seller_id", serializers)
        self.assertNotIn('"email"', serializers)
        self.assertNotIn('"phone"', serializers)

    def test_seller_schema_has_lifecycle_identity_and_concurrency_fields(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        fields = {field.get("fieldname"): field for field in schema["fields"]}
        for fieldname in (
            "public_id",
            "status_changed_at",
            "status_reason_code",
            "status_source",
            "storefront_version",
            "storefront_updated_at",
        ):
            self.assertIn(fieldname, fields)
        self.assertTrue(fields["public_id"].get("unique"))
        self.assertTrue(fields["public_id"].get("read_only"))
        status_options = fields["status"]["options"].splitlines()
        self.assertEqual(status_options, ["Active", "Suspended", "Deleted"])
        hours_schema = json.loads(
            (ROOT / "aos/aos/doctype/aos_seller_operating_hours/aos_seller_operating_hours.json").read_text()
        )
        day_field = next(field for field in hours_schema["fields"] if field.get("fieldname") == "day_of_week")
        self.assertEqual(
            day_field["options"].splitlines(),
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
        )

    def test_storefront_service_uses_lock_version_media_and_no_commit(self):
        source = (ROOT / "aos/services/sellers/service.py").read_text()
        self.assertIn("FOR UPDATE", source)
        self.assertIn("expected_version", source)
        self.assertIn('purpose="seller_banner"', source)
        self.assertIn("replacing_media_id", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_public_discovery_bulk_loads_identity_media_live_and_relationship_state(self):
        serializers = (ROOT / "aos/services/sellers/serializers.py").read_text()
        media = (ROOT / "aos/services/media/media_service.py").read_text()
        service = (ROOT / "aos/services/sellers/service.py").read_text()
        self.assertIn("WHERE u.name IN %(users)s", serializers)
        self.assertIn("get_public_url_map", serializers)
        self.assertIn("get_users_live_state", serializers)
        self.assertIn("_friend_counts(users)", service)
        self.assertIn("_relationship_map", service)
        self.assertIn("def get_public_url_map", media)

    def test_seller_metrics_have_canonical_aggregate_reconciliation(self):
        aggregate = (ROOT / "aos/services/sellers/aggregates.py").read_text()
        ad_controller = (ROOT / "aos/aos/doctype/aos_ad/aos_ad.py").read_text()
        self.assertIn("def reconcile_seller_ad_counts", aggregate)
        self.assertIn("status = 'Active'", aggregate)
        self.assertIn("GREATEST(COALESCE(total_ads, 0) + %s, 0)", ad_controller)
        self.assertIn('self.status == "Active"', ad_controller)
        self.assertNotIn("frappe.db.commit", aggregate)

    def test_seller_patch_is_registered_bounded_idempotent_and_non_committing(self):
        patches = (ROOT / "aos/patches.txt").read_text()
        source = (ROOT / "aos/patches/v1_0/harden_sellers_subsystem.py").read_text()
        self.assertIn("aos.patches.v1_0.harden_sellers_subsystem", patches)
        self.assertIn("_BATCH_SIZE = 250", source)
        self.assertIn("LIMIT %s", source)
        self.assertIn("uq_aos_seller_public_id", source)
        self.assertIn("_canonical_public_id", source)
        self.assertIn("SELECT MIN(name)", source)
        self.assertNotIn("frappe.db.commit", source)
        operating_patch = "aos.patches.v1_0.canonicalize_seller_operating_days"
        self.assertIn(operating_patch, patches)
        operating_source = (
            ROOT / "aos/patches/v1_0/canonicalize_seller_operating_days.py"
        ).read_text()
        self.assertIn("_BATCH_SIZE = 250", operating_source)
        self.assertIn("LIMIT %(limit)s", operating_source)
        self.assertNotIn("frappe.db.commit", operating_source)

    def test_location_compatibility_endpoints_return_public_seller_ids(self):
        for filename in ("get_location.py", "set_location.py", "remove_location.py", "map_points.py"):
            source = (ROOT / "aos/api/sellers" / filename).read_text()
            self.assertTrue(
                "public_seller_id_for_name" in source
                or "migration_fallback_public_seller_id" in source,
                filename,
            )
            self.assertNotIn("frappe.db.commit", source)
        route = (ROOT / "aos/api/maps/route.py").read_text()
        self.assertIn("resolve_seller_reference", route)

    def test_every_seller_endpoint_has_rate_limit_coverage(self):
        registry = json.loads((ROOT / "ci/public-endpoint-rate-limits.json").read_text())
        endpoints = {row["endpoint"] for row in registry}
        tree = ast.parse((ROOT / "aos/api/v1/sellers/__init__.py").read_text())
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
        expected = {f"aos.api.v1.sellers.__init__.{name}" for name in functions}
        self.assertTrue(expected <= endpoints)

    def test_error_codes_have_stable_http_mappings(self):
        source = (ROOT / "aos/api/shared/responses.py").read_text()
        for code in (
            "INVALID_SELLER_REQUEST",
            "INVALID_SELLER_SORT",
            "SELLER_ACCESS_DENIED",
            "SELLER_NOT_FOUND",
            "SELLER_VERSION_CONFLICT",
        ):
            self.assertIn(f'"{code}"', source)
