from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestSellerContracts(unittest.TestCase):
    def test_v1_surface_is_strict_and_transport_clean(self):
        source = (ROOT / "aos/api/v1/sellers/__init__.py").read_text()
        self.assertIn("_client_kwargs(kwargs)", source)
        self.assertNotIn('methods=["GET", "POST"]', source)
        self.assertNotIn("frappe.db", source)

    def test_public_identity_is_opaque_only_with_random_internal_names(self):
        identity = (ROOT / "aos/services/sellers/identity.py").read_text()
        self.assertIn(r"^SELLER-[A-Z2-7]{20}$", identity)
        self.assertNotIn("resolve_seller_reference", identity)
        self.assertNotIn("migration_fallback", identity)
        schema = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        self.assertEqual(schema.get("autoname"), "hash")
        self.assertEqual(schema.get("naming_rule"), "Random")

    def test_lifecycle_is_single_operational_state_machine(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        fields = {field.get("fieldname"): field for field in schema["fields"]}
        self.assertEqual(fields["status"]["options"].splitlines(), ["Active", "Suspended", "Closed"])
        policy = (ROOT / "aos/services/sellers/policy.py").read_text()
        self.assertIn("STATUS_CLOSED", policy)
        self.assertIn("_ALLOWED_TRANSITIONS", policy)
        self.assertIn("sync_verification_projection", policy)
        self.assertNotIn("verified = bool", policy)

    def test_media_contract_has_no_cached_url_field_or_aliases(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        fields = {field.get("fieldname") for field in schema["fields"]}
        self.assertNotIn("shop_banner", fields)
        self.assertIn("shop_banner_media", fields)
        service = (ROOT / "aos/services/sellers/service.py").read_text()
        self.assertIn('request.get("shop_banner_media_id")', service)
        self.assertNotIn('request.get("shop_banner")', service)
        self.assertNotIn('request.get("banner_media")', service)
        self.assertNotIn('request.get("media_id")', service)

    def test_location_persistence_is_owned_by_sellers_and_maps_consumes_lookup(self):
        seller_location = (ROOT / "aos/services/sellers/location.py").read_text()
        seller_repository = (ROOT / "aos/services/sellers/repository.py").read_text()
        maps_service = (ROOT / "aos/services/maps/service.py").read_text()
        self.assertIn("FOR UPDATE", seller_repository)
        self.assertIn("expected_version", seller_location)
        self.assertIn("SELLER_LOCATION_VERSION_CONFLICT", seller_location)
        self.assertIn("get_route_destination", maps_service)
        self.assertNotIn("def set_seller_location", maps_service)
        self.assertNotIn("def remove_seller_location", maps_service)
        self.assertFalse((ROOT / "aos/services/maps/repository.py").exists())
        self.assertFalse((ROOT / "aos/api/maps/seller_locations.py").exists())

    def test_discovery_is_bounded_keyset_and_global(self):
        discovery = (ROOT / "aos/services/sellers/discovery.py").read_text()
        geo = (ROOT / "aos/services/sellers/geo.py").read_text()
        constants = (ROOT / "aos/services/sellers/constants.py").read_text()
        self.assertIn("decode_cursor", discovery)
        self.assertIn("encode_cursor", discovery)
        self.assertNotIn(" OFFSET ", discovery.upper())
        self.assertIn("limit + 1", discovery)
        self.assertIn("crosses_antimeridian", discovery)
        self.assertIn("_normalize_longitude", geo)
        self.assertIn("MAX_LIST_LIMIT = 50", constants)
        self.assertNotIn("Kenya", discovery + geo)

    def test_discovery_cursor_is_bound_to_exact_viewer_without_exposing_identity(self):
        discovery = (ROOT / "aos/services/sellers/discovery.py").read_text()
        pagination = (ROOT / "aos/services/sellers/pagination.py").read_text()
        self.assertIn('"viewer_scope": str(viewer) if authenticated else "guest"', discovery)
        self.assertNotIn('"viewer_scope": "authenticated" if authenticated else "guest"', discovery)
        self.assertIn("hashlib.sha256", pagination)
        self.assertNotIn('"viewer_scope"', pagination)

    def test_creation_and_verification_projection_are_lock_safe(self):
        policy = (ROOT / "aos/services/sellers/policy.py").read_text()
        repository = (ROOT / "aos/services/sellers/repository.py").read_text()
        self.assertIn("lock_verification_for_user(clean_user)", policy)
        self.assertIn("FOR UPDATE", repository)
        self.assertIn("doc = lock_by_name(row.name)", policy)
        self.assertIn("for attempt in range(8)", policy)
        self.assertIn("rollback(save_point=savepoint)", policy)
        self.assertIn("SELLER_CREATE_CONFLICT", policy)

    def test_storefront_mutation_uses_lock_version_media_and_savepoint(self):
        service = (ROOT / "aos/services/sellers/service.py").read_text()
        boundary = (ROOT / "aos/services/sellers/api.py").read_text()
        self.assertIn("FOR UPDATE", service)
        self.assertIn("expected_version", service)
        self.assertIn('purpose="seller_banner"', service)
        self.assertIn("frappe.db.savepoint(savepoint)", boundary)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", boundary)
        self.assertNotIn("frappe.db.commit", service + boundary)

    def test_fresh_site_has_one_doc_no_seller_compatibility_patches(self):
        docs = sorted(path.name for path in (ROOT / "docs/features/sellers").glob("*.md"))
        self.assertEqual(docs, ["README.md"])
        patches = (ROOT / "aos/patches.txt").read_text()
        self.assertNotIn("harden_sellers_subsystem", patches)
        self.assertNotIn("canonicalize_seller_operating_days", patches)
        self.assertFalse((ROOT / "aos/patches/v1_0/harden_sellers_subsystem.py").exists())
        self.assertFalse((ROOT / "aos/patches/v1_0/canonicalize_seller_operating_days.py").exists())

    def test_current_seller_schema_installer_owns_location_indexes(self):
        migrate = (ROOT / "aos/migrate.py").read_text()
        schema = (ROOT / "aos/services/sellers/schema.py").read_text()
        self.assertIn("seller_schema.execute", migrate)
        self.assertNotIn("maps_schema.execute", migrate)
        self.assertIn("idx_aos_seller_location_lat_lon", schema)
        self.assertFalse((ROOT / "aos/services/maps/schema.py").exists())

    def test_public_serialization_does_not_expose_future_chat_live_or_internal_ids(self):
        serializers = (ROOT / "aos/services/sellers/serializers.py").read_text()
        self.assertNotIn("get_users_live_state", serializers)
        self.assertNotIn("chat_response", serializers)
        self.assertNotIn('"email"', serializers)
        self.assertNotIn('"phone"', serializers)
        self.assertNotIn('"name": _public_id', serializers)

    def test_every_seller_endpoint_has_rate_limit_coverage(self):
        registry = json.loads((ROOT / "ci/public-endpoint-rate-limits.json").read_text())
        endpoints = {row["endpoint"] for row in registry}
        tree = ast.parse((ROOT / "aos/api/v1/sellers/__init__.py").read_text())
        functions = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
            and any(isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "whitelist" for d in node.decorator_list)
        }
        expected = {f"aos.api.v1.sellers.__init__.{name}" for name in functions}
        self.assertTrue(expected <= endpoints)

    def test_stable_seller_error_codes_are_registered(self):
        responses = (ROOT / "aos/api/shared/responses.py").read_text()
        for code in (
            "INVALID_SELLER_REQUEST", "INVALID_SELLER_PAGINATION", "SELLER_ACCESS_DENIED",
            "SELLER_NOT_FOUND", "SELLER_VERSION_CONFLICT", "SELLER_CREATE_CONFLICT",
            "SELLER_LOCATION_VERSION_CONFLICT",
            "SELLER_LOCATION_UNRESOLVED", "SELLER_STATUS_TRANSITION_NOT_ALLOWED",
        ):
            self.assertIn(f'"{code}"', responses)
