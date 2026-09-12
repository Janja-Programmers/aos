from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]


class TestMapsContracts(unittest.TestCase):
    def test_v1_search_surface_is_get_only_and_transport_clean(self):
        source = (ROOT / "aos/api/v1/maps/__init__.py").read_text()
        self.assertIn("_client_kwargs(kwargs)", source)
        self.assertEqual(source.count('allow_guest=True,\n    methods=["GET"]'), 3)
        self.assertNotIn('methods=["GET", "POST"]', source)
        self.assertNotIn("frappe.db", source)

    def test_public_endpoints_are_thin_maps_service_boundaries(self):
        for filename in ("autocomplete.py", "search.py", "reverse.py", "route.py"):
            source = (ROOT / "aos/api/maps" / filename).read_text()
            self.assertIn("MapsService", source, filename)
            self.assertIn("run_maps_api", source, filename)
            self.assertNotIn("frappe.db", source, filename)

    def test_no_kenya_or_nairobi_coverage_assumptions_remain_in_runtime_maps(self):
        runtime_roots = [ROOT / "aos/api/maps", ROOT / "aos/services/maps"]
        for base in runtime_roots:
            for path in base.rglob("*.py"):
                if "/tests/" in path.as_posix():
                    continue
                text = path.read_text()
                self.assertNotIn("KENYA", text, path)
                self.assertNotIn("Kenya", text, path)
                self.assertNotIn("Nairobi", text, path)

    def test_photon_is_canonical_and_nominatim_only_explicit_fallback(self):
        providers = (ROOT / "aos/services/maps/providers.py").read_text()
        self.assertIn("GEOCODER_PRIMARY_PHOTON", providers)
        self.assertIn("maps_nominatim_fallback_enabled", providers)
        self.assertNotIn("maps_geocoder_primary", providers)
        self.assertNotIn("maps_geocoder_fallback", providers)

    def test_provider_urls_are_ssrf_hardened(self):
        source = (ROOT / "aos/services/maps/internal_url.py").read_text()
        self.assertIn("ipaddress.ip_address", source)
        self.assertIn("parsed.username", source)
        self.assertIn("address.is_private", source)

    def test_place_identity_is_provider_neutral(self):
        source = (ROOT / "aos/api/maps/serializers.py").read_text()
        self.assertIn('return f"osm:{normalized_type}:{osm_id}"', source)
        self.assertNotIn('"source": "photon"', source)
        self.assertNotIn('"source": "nominatim"', source)

    def test_maps_mutations_use_lock_version_and_current_schema_installer(self):
        service = (ROOT / "aos/services/maps/service.py").read_text()
        repository = (ROOT / "aos/services/maps/repository.py").read_text()
        boundary = (ROOT / "aos/services/maps/api.py").read_text()
        self.assertIn("FOR UPDATE", repository)
        self.assertIn("expected_version", service)
        self.assertIn("location_version", service)
        self.assertIn("frappe.db.savepoint(savepoint)", boundary)
        self.assertNotIn("frappe.db.commit", service + repository + boundary)
        patches = (ROOT / "aos/patches.txt").read_text()
        self.assertNotIn("harden_maps_subsystem", patches)
        self.assertNotIn("add_seller_location_indexes", patches)
        migrate = (ROOT / "aos/migrate.py").read_text()
        self.assertIn("maps_schema.execute", migrate)

    def test_cache_keys_and_logs_do_not_embed_queries_or_coordinates(self):
        cache = (ROOT / "aos/services/maps/cache.py").read_text()
        observability = (ROOT / "aos/services/maps/observability.py").read_text()
        self.assertIn("hashlib.sha256", cache)
        self.assertNotIn('"query":', observability)
        self.assertNotIn('"latitude":', observability)
        self.assertNotIn('"longitude":', observability)

    def test_compose_has_private_resource_bounded_maps_services(self):
        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertIn("  photon:", compose)
        self.assertIn("PHOTON_OPENSEARCH_TRANSPORT_ADDRESSES", compose)
        self.assertIn('profiles: ["maps-routing"]', compose)
        self.assertNotIn("  tileserver:\n", compose)
        self.assertNotIn("  nominatim:\n", compose)
        self.assertIn("mem_limit:", compose)
        self.assertIn("stop_grace_period:", compose)

    def test_single_maps_feature_document_and_no_legacy_maps_patch(self):
        docs = sorted(path.name for path in (ROOT / "docs/features/maps").glob("*.md"))
        self.assertEqual(docs, ["README.md"])
        self.assertFalse((ROOT / "aos/patches/v1_0/harden_maps_subsystem.py").exists())
        self.assertFalse((ROOT / "aos/patches/v1_0/add_seller_location_indexes.py").exists())

    def test_seller_location_schema_retains_optimistic_concurrency(self):
        schema = json.loads((ROOT / "aos/aos/doctype/aos_seller/aos_seller.json").read_text())
        fields = {field.get("fieldname"): field for field in schema["fields"]}
        self.assertIn("location_version", fields)
        self.assertTrue(fields["location_version"].get("read_only"))

    def test_legacy_outside_coverage_error_is_removed(self):
        responses = (ROOT / "aos/api/shared/responses.py").read_text()
        self.assertNotIn('"MAP_OUTSIDE_COVERAGE"', responses)
