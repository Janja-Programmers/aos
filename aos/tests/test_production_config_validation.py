from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.diagnostics import get_production_config_status
from aos.utils.production_config import (
    ProductionConfigError,
    assert_production_config_ready,
    report_contains_secret_value,
    validate_production_config,
)


class TestProductionConfigValidation(FrappeTestCase):
    """Focused tests for production-readiness config diagnostics."""

    def setUp(self):
        frappe.local.response = {}

    def _valid_env(self) -> dict[str, str]:
        return {
            "AOS_API_DOMAIN": "api.africaonlinestores.example-prod.com",
            "AOS_MAPS_DOMAIN": "maps.africaonlinestores.example-prod.com",
            "AOS_MINIO_DOMAIN": "files.africaonlinestores.example-prod.com",
            "MINIO_ENDPOINT": "127.0.0.1:9100",
            "MINIO_ROOT_USER": "aos_minio_prod_user",
            "MINIO_ROOT_PASSWORD": "minio-prod-secret-value-0123456789abcdef",
            "MINIO_PUBLIC_BASE_URL": "https://files.africaonlinestores.example-prod.com",
            "AOS_PUBLIC_BUCKET": "aos-public",
            "AOS_PRIVATE_BUCKET": "aos-private",
            "AOS_MINIO_BUCKET": "shorts",
            "LIVEKIT_ENDPOINT": "wss://live.africaonlinestores.example-prod.com",
            "LIVEKIT_API_KEY": "aos_livekit_prod_key",
            "LIVEKIT_API_SECRET": "livekit-prod-secret-value-0123456789abcdef",
            "VIDEO_SERVICE_URL": "http://127.0.0.1:8130",
            "VIDEO_SERVICE_SECRET": "video-dispatch-secret-value-0123456789abcdef",
            "VIDEO_SERVICE_CALLBACK_SECRET": "video-callback-secret-value-0123456789abcdef",
            "VIDEO_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.video_processing.handle_callback",
            "MODERATION_ENABLED": "true",
            "MODERATION_SERVICE_URL": "http://127.0.0.1:8140",
            "MODERATION_SERVICE_SECRET": "moderation-dispatch-secret-value-0123456789abcdef",
            "MODERATION_SERVICE_CALLBACK_SECRET": "moderation-callback-secret-value-0123456789abcdef",
            "MODERATION_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.moderation.handle_callback",
            "SEARCH_RANKING_ENABLED": "true",
            "SEARCH_RANKING_SERVICE_URL": "http://127.0.0.1:8150",
            "SEARCH_RANKING_SERVICE_SECRET": "search-dispatch-secret-value-0123456789abcdef",
            "SEARCH_RANKING_SERVICE_CALLBACK_SECRET": "search-callback-secret-value-0123456789abcdef",
            "SEARCH_RANKING_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.search_ranking.handle_callback",
            "ANALYTICS_PIPELINE_ENABLED": "true",
            "ANALYTICS_SERVICE_URL": "http://127.0.0.1:8170",
            "ANALYTICS_SERVICE_SECRET": "analytics-dispatch-secret-value-0123456789abcdef",
            "ANALYTICS_SERVICE_CALLBACK_SECRET": "analytics-callback-secret-value-0123456789abcdef",
            "ANALYTICS_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.analytics_pipeline.handle_callback",
            "NOTIFICATION_DELIVERY_ENABLED": "true",
            "NOTIFICATION_DRY_RUN": "false",
            "NOTIFICATION_SERVICE_URL": "http://127.0.0.1:8160",
            "NOTIFICATION_SERVICE_SECRET": "notification-dispatch-secret-value-0123456789abcdef",
            "NOTIFICATION_SERVICE_CALLBACK_SECRET": "notification-callback-secret-value-0123456789abcdef",
            "NOTIFICATION_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.notification_delivery.handle_callback",
            "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH": "/run/secrets/firebase-service-account.json",
            "TRANSLATION_SERVICE_URL": "http://127.0.0.1:8100",
            "IMAGE_SEARCH_SERVICE_URL": "http://127.0.0.1:8110",
            "BACKGROUND_REMOVAL_SERVICE_URL": "http://127.0.0.1:8120",
            "IMAGE_SEARCH_QDRANT_URL": "http://qdrant:6333",
            "TILESERVER_PUBLIC_URL": "https://maps.africaonlinestores.example-prod.com/",
        }

    def _valid_site_config(self) -> dict[str, str]:
        return {
            "photon_base_url": "http://127.0.0.1:2322",
            "nominatim_base_url": "http://127.0.0.1:8081",
            "valhalla_base_url": "http://127.0.0.1:8002",
        }

    def test_complete_production_config_is_ready_and_redacted(self):
        env = self._valid_env()
        report = validate_production_config(
            env=env,
            site_config=self._valid_site_config(),
        )

        self.assertTrue(report["ready"], report)
        self.assertEqual(report["summary"]["errors"], 0)
        self.assertFalse(
            report_contains_secret_value(report, env["VIDEO_SERVICE_SECRET"]),
            "Secret values must never appear in the diagnostic report.",
        )
        self.assertFalse(
            report_contains_secret_value(report, env["MINIO_ROOT_PASSWORD"]),
            "Storage secret values must never appear in the diagnostic report.",
        )

    def test_missing_and_placeholder_values_are_reported_without_leaking_values(self):
        env = self._valid_env()
        env["VIDEO_SERVICE_CALLBACK_SECRET"] = "change-this-video-callback-secret"
        env["MINIO_PUBLIC_BASE_URL"] = "http://localhost:9100"
        env.pop("LIVEKIT_API_SECRET")

        report = validate_production_config(
            env=env,
            site_config=self._valid_site_config(),
        )

        self.assertFalse(report["ready"])
        keys = {issue["key"] for issue in report["errors"]}
        self.assertIn("VIDEO_SERVICE_CALLBACK_SECRET", keys)
        self.assertIn("MINIO_PUBLIC_BASE_URL", keys)
        self.assertIn("LIVEKIT_API_SECRET/LIVEKIT_KEYS", keys)
        self.assertFalse(report_contains_secret_value(report, "change-this-video-callback-secret"))

    def test_callback_urls_can_be_derived_from_aos_api_domain(self):
        env = self._valid_env()
        for key in (
            "VIDEO_CALLBACK_URL",
            "MODERATION_CALLBACK_URL",
            "SEARCH_RANKING_CALLBACK_URL",
            "ANALYTICS_CALLBACK_URL",
            "NOTIFICATION_CALLBACK_URL",
        ):
            env.pop(key)

        report = validate_production_config(
            env=env,
            site_config=self._valid_site_config(),
        )

        self.assertTrue(report["ready"], report)

    def test_maps_site_config_is_required(self):
        report = validate_production_config(
            env=self._valid_env(),
            site_config={},
        )

        self.assertFalse(report["ready"])
        keys = {issue["key"] for issue in report["errors"]}
        self.assertIn("photon_base_url", keys)
        self.assertIn("nominatim_base_url", keys)
        self.assertIn("valhalla_base_url", keys)

    def test_assert_production_config_ready_raises_for_invalid_runtime_config(self):
        with patch(
            "aos.utils.production_config.validate_production_config",
            return_value={"ready": False, "summary": {"errors": 2}, "errors": [], "warnings": []},
        ):
            with self.assertRaises(ProductionConfigError):
                assert_production_config_ready()

    def test_admin_diagnostic_requires_system_manager(self):
        with patch("aos.api.diagnostics.frappe.session", type("Session", (), {"user": "guest@example.com"})()):
            with patch("aos.api.diagnostics.frappe.get_roles", return_value=[]):
                response = get_production_config_status()

        self.assertFalse(response["ok"])
        self.assertEqual(response["code"], "PERMISSION_DENIED")
        self.assertEqual(frappe.local.response.get("http_status_code"), 403)

    def test_admin_diagnostic_returns_redacted_report_for_system_manager(self):
        report = {"ready": True, "summary": {"errors": 0, "warnings": 0}, "errors": [], "warnings": []}
        with patch("aos.api.diagnostics.frappe.session", type("Session", (), {"user": "admin@example.com"})()):
            with patch("aos.api.diagnostics.frappe.get_roles", return_value=["System Manager"]):
                with patch("aos.api.diagnostics.validate_production_config", return_value=report):
                    response = get_production_config_status()

        self.assertTrue(response["ok"])
        self.assertEqual(response["data"], report)
        self.assertEqual(frappe.local.response.get("http_status_code"), 200)
