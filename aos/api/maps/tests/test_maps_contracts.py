from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestMapsContracts(unittest.TestCase):
    def test_v1_maps_wrappers_strip_framework_transport_metadata(self):
        source = (ROOT / "aos/api/v1/maps/__init__.py").read_text()
        self.assertIn("_client_kwargs(kwargs)", source)
        self.assertNotIn("frappe.db", source)

    def test_public_endpoints_are_thin_maps_service_boundaries(self):
        for filename in ("autocomplete.py", "search.py", "reverse.py", "route.py"):
            source = (ROOT / "aos/api/maps" / filename).read_text()
            self.assertIn("MapsService", source, filename)
            self.assertIn("run_maps_api", source, filename)
            self.assertNotIn("frappe.db", source, filename)
            self.assertNotIn("frappe.db.commit", source, filename)

    def test_seller_compatibility_endpoints_delegate_to_maps(self):
        for filename in ("map_points.py", "get_location.py", "set_location.py", "remove_location.py"):
            source = (ROOT / "aos/api/sellers" / filename).read_text()
            self.assertIn("aos.api.maps.seller_locations", source)
            self.assertNotIn("frappe.db", source)

    def test_maps_mutations_use_lock_version_and_operation_savepoints(self):
        service = (ROOT / "aos/services/maps/service.py").read_text()
        repository = (ROOT / "aos/services/maps/repository.py").read_text()
        boundary = (ROOT / "aos/services/maps/api.py").read_text()
        self.assertIn("FOR UPDATE", repository)
        self.assertIn("expected_version", service)
        self.assertIn("location_version", service)
        self.assertIn("frappe.db.savepoint(savepoint)", boundary)
        self.assertIn("frappe.db.rollback(save_point=savepoint)", boundary)
        self.assertNotIn("frappe.db.commit", service + repository + boundary)

    def test_schema_controller_and_patch_protect_location_concurrency(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        fields = {field.get("fieldname"): field for field in schema["fields"]}
        self.assertIn("location_version", fields)
        self.assertTrue(fields["location_version"].get("read_only"))
        controller = (ROOT / "aos/aos/doctype/aos_seller/aos_seller.py").read_text()
        self.assertIn("aos_seller_location_action", controller)
        patches = (ROOT / "aos/patches.txt").read_text()
        patch = (ROOT / "aos/patches/v1_0/harden_maps_subsystem.py").read_text()
        self.assertIn("aos.patches.v1_0.harden_maps_subsystem", patches)
        self.assertIn("LIMIT %s", patch)
        self.assertNotIn("frappe.db.commit", patch)

    def test_cache_keys_and_logs_do_not_embed_queries_or_coordinates(self):
        cache = (ROOT / "aos/services/maps/cache.py").read_text()
        observability = (ROOT / "aos/services/maps/observability.py").read_text()
        self.assertIn("hashlib.sha256", cache)
        self.assertNotIn('"query":', observability)
        self.assertNotIn('"latitude":', observability)
        self.assertNotIn('"longitude":', observability)

    def test_provider_urls_are_ssrf_hardened_and_photon_is_explicit(self):
        source = (ROOT / "aos/services/maps/internal_url.py").read_text()
        providers = (ROOT / "aos/services/maps/providers.py").read_text()
        self.assertIn("ipaddress.ip_address", source)
        self.assertIn("parsed.username", source)
        self.assertIn("maps_photon_enabled", providers)
        constants = (ROOT / "aos/api/maps/constants.py").read_text()
        self.assertIn("DEFAULT_GEOCODER_PRIMARY = GEOCODER_PRIMARY_NOMINATIM", constants)

    def test_maps_error_codes_have_stable_http_mappings(self):
        responses = (ROOT / "aos/api/shared/responses.py").read_text()
        for code in (
            "INVALID_MAP_REQUEST",
            "MAP_OUTSIDE_COVERAGE",
            "MAP_ACCESS_DENIED",
            "MAP_LOCATION_NOT_FOUND",
            "MAP_LOCATION_UNAVAILABLE",
            "MAP_LOCATION_VERSION_CONFLICT",
            "MAP_SERVICE_ERROR",
        ):
            self.assertIn(f'"{code}"', responses)
