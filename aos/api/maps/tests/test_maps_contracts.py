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

    def test_photon_adapter_normalizes_browser_languages_and_uses_strong_soft_location_bias(self):
        client = (ROOT / "aos/api/maps/clients/photon_client.py").read_text()
        constants = (ROOT / "aos/api/maps/constants.py").read_text()
        self.assertIn("_canonical_photon_language(language)", client)
        self.assertIn('split("-", 1)[0]', client)
        self.assertIn("PHOTON_SUPPORTED_LANGUAGES_CONFIG_KEY", client)
        self.assertIn('PHOTON_DEFAULT_SUPPORTED_LANGUAGES = ("en",)', constants)
        self.assertIn('"location_bias_scale": PHOTON_LOCATION_BIAS_SCALE', client)

    def test_basemap_publication_matches_unsigned_read_only_nginx_origin(self):
        publish = (ROOT / "infra/maps/scripts/publish-basemap.py").read_text()
        nginx = (ROOT / "infra/nginx/maps.conf.template").read_text()
        env_example = (ROOT / ".env.example").read_text()
        verifier = ROOT / "infra/maps/scripts/verify-basemap-origin.py"
        self.assertIn('"Action": ["s3:GetObject"]', publish)
        self.assertIn('"Resource": [f"arn:aws:s3:::{bucket}/basemap/*"]', publish)
        self.assertIn("client.set_bucket_policy", publish)
        self.assertIn('policy_only = args == ["--policy-only"]', publish)
        self.assertIn('env("MAPS_OBJECT_STORAGE_POLICY_ACCESS_KEY")', publish)
        self.assertIn('env("MAPS_OBJECT_STORAGE_POLICY_SECRET_KEY")', publish)
        self.assertIn('env("MAPS_OBJECT_STORAGE_ACCESS_KEY")', publish)
        self.assertIn('env("MAPS_OBJECT_STORAGE_SECRET_KEY")', publish)
        self.assertLess(publish.index("if policy_only:"), publish.index('return _publish(root, bucket)'))
        self.assertIn("MAPS_OBJECT_STORAGE_POLICY_ACCESS_KEY", env_example)
        self.assertIn("MAPS_OBJECT_STORAGE_POLICY_SECRET_KEY", env_example)
        self.assertNotIn("MAPS_OBJECT_STORAGE_ALLOW_ANONYMOUS_READ", publish)
        self.assertNotIn("MAPS_OBJECT_STORAGE_ALLOW_ANONYMOUS_READ", env_example)
        self.assertIn('proxy_set_header Authorization "";', nginx)
        self.assertIn('add_header Accept-Ranges "bytes" always;', nginx)
        self.assertTrue(verifier.exists())
        verifier_source = verifier.read_text()
        self.assertIn("tile.status_code != 206", verifier_source)
        self.assertIn('startswith(b"PMTiles")', verifier_source)

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

    def test_seller_location_mutation_is_outside_maps_and_route_uses_seller_contract(self):
        service = (ROOT / "aos/services/maps/service.py").read_text()
        seller_location = (ROOT / "aos/services/sellers/location.py").read_text()
        seller_repository = (ROOT / "aos/services/sellers/repository.py").read_text()
        self.assertNotIn("def set_seller_location", service)
        self.assertNotIn("def remove_seller_location", service)
        self.assertIn("get_route_destination", service)
        self.assertIn("FOR UPDATE", seller_repository)
        self.assertIn("expected_version", seller_location)
        self.assertFalse((ROOT / "aos/services/maps/repository.py").exists())
        migrate = (ROOT / "aos/migrate.py").read_text()
        self.assertIn("seller_schema.execute", migrate)

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
        self.assertIn('entrypoint: ["valhalla_service"]', compose)
        self.assertIn(':/custom_files:ro', compose)
        self.assertNotIn("  tileserver:\n", compose)
        self.assertNotIn("  nominatim:\n", compose)
        self.assertIn("mem_limit:", compose)
        self.assertIn("stop_grace_period:", compose)

    def test_routing_feature_flag_is_enforced_before_valhalla_calls(self):
        providers = (ROOT / "aos/services/maps/providers.py").read_text()
        service = (ROOT / "aos/services/maps/service.py").read_text()
        production = (ROOT / "aos/utils/production_config.py").read_text()
        self.assertIn('MAPS_ROUTING_ENABLED_CONFIG_KEY = "maps_routing_enabled"', providers)
        self.assertIn("if not routing_enabled():", service)
        self.assertIn("Valhalla routing must be enabled for production Maps.", production)

    def test_legacy_tileserver_font_pipeline_is_removed(self):
        self.assertFalse((ROOT / "aos/tests/test_map_font_assets.py").exists())
        self.assertFalse((ROOT / "infra/maps/scripts/build-map-fonts.sh").exists())

        gitignore = (ROOT / ".gitignore").read_text()
        self.assertNotIn("infra/maps/tileserver", gitignore)

        image_evidence = (ROOT / "ci/image-manifest-evidence.txt").read_text()
        self.assertNotIn("maptiler/tileserver-gl", image_evidence)

        compose = (ROOT / "docker-compose.yml").read_text()
        self.assertNotIn("tileserver", compose.lower())

    def test_photon_metrics_flag_supplies_required_prometheus_type(self):
        entrypoint = (ROOT / "infra/maps/photon/entrypoint.sh").read_text()
        self.assertIn('metrics_arg="-metrics-enable prometheus"', entrypoint)
        self.assertNotIn('metrics_arg="-metrics-enable"; fi', entrypoint)

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
