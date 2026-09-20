from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.v1.diagnostics import get_operational_health_status
from aos.utils.operational_health import validate_operational_health


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True, "ready": True, "service": "fake"}

    def json(self):
        return self._payload


class _FakeStorageClient:
    def list_buckets(self):
        return [object(), object()]


class _FakeStorageConfig:
    endpoint = "127.0.0.1:9100"
    public_bucket = "aos-public"
    private_bucket = "aos-private"
    bucket = "shorts"


class _FakeStorage:
    config = _FakeStorageConfig()
    client = _FakeStorageClient()

    def healthcheck(self):
        return {
            "ok": True,
            "latency_ms": 1,
            "configured_bucket_count": 3,
            "missing_bucket_count": 0,
        }


class _FakeCache:
    def ping(self):
        return True


class TestOperationalHealth(FrappeTestCase):
    """Focused tests for admin-only operational health diagnostics."""

    def setUp(self):
        frappe.local.response = {}
        self._firebase_tempdir = tempfile.TemporaryDirectory(prefix="aos-operational-health-")
        self._firebase_path = Path(self._firebase_tempdir.name) / "firebase-service-account.json"
        self._firebase_path.write_text(
            json.dumps(
                {
                    "type": "service_account",
                    "project_id": "aos-production",
                    "client_email": "firebase-admin@aos-production.invalid",
                }
            ),
            encoding="utf-8",
        )
        os.chmod(self._firebase_path, 0o600)

    def tearDown(self):
        self._firebase_tempdir.cleanup()
        super().tearDown()

    def _valid_env(self) -> dict[str, str]:
        return {
            "AOS_ENVIRONMENT": "production",
            "BACKUP_ENCRYPTION_REQUIRED": "true",
            "BACKUP_ENCRYPTION_METHOD": "age",
            "BACKUP_LOCAL_RETENTION_MODE": "encrypted-artifact",
            "BACKUP_AGE_RECIPIENT": "age1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            "AOS_METRICS_TOKEN": "metrics-secret-value-0123456789abcdef",
            "AOS_ALERTING_ENABLED": "true",
            "BACKUP_FAILURE_NOTIFICATION_METHOD": "email",
            "BACKUP_FAILURE_EMAIL_TO": "ops@africaonlinestores.com",
            "AOS_API_DOMAIN": "api.africaonlinestores.example-prod.com",
            "AOS_MAPS_DOMAIN": "maps.africaonlinestores.example-prod.com",
            "AOS_MINIO_DOMAIN": "files.africaonlinestores.example-prod.com",
            "AOS_OBJECT_STORAGE_ENDPOINT": "objects.africaonlinestores.co.ke",
            "AOS_OBJECT_STORAGE_SECURE": "true",
            "AOS_OBJECT_STORAGE_PATH_STYLE": "false",
            "AOS_OBJECT_STORAGE_PRESIGN_ENDPOINT": "https://objects.africaonlinestores.co.ke",
            "AOS_MEDIA_PUBLIC_BASE_URL": "https://media.africaonlinestores.co.ke",
            "AOS_OBJECT_STORAGE_PUBLIC_BUCKET": "aos-media-public",
            "AOS_OBJECT_STORAGE_PRIVATE_BUCKET": "aos-media-private",
            "AOS_OBJECT_STORAGE_ACCESS_KEY": "aos_media_prod_access_0123456789",
            "AOS_OBJECT_STORAGE_SECRET_KEY": "media-storage-secret-value-0123456789abcdef",
            "AOS_OBJECT_STORAGE_MANAGE_BUCKETS": "false",
            "MINIO_ENDPOINT": "127.0.0.1:9100",
            "MINIO_ROOT_USER": "aos_minio_prod_user",
            "MINIO_ROOT_PASSWORD": "minio-prod-secret-value-0123456789abcdef",
            "MINIO_PUBLIC_BASE_URL": "https://files.africaonlinestores.example-prod.com",
            "AOS_PUBLIC_BUCKET": "aos-public",
            "AOS_PRIVATE_BUCKET": "aos-private",
            "LIVEKIT_ENDPOINT": "wss://live.africaonlinestores.example-prod.com",
            "LIVEKIT_API_KEY": "aos_livekit_prod_key",
            "LIVEKIT_API_SECRET": "livekit-prod-secret-value-0123456789abcdef",
            "VIDEO_SERVICE_URL": "http://127.0.0.1:8130",
            "VIDEO_SERVICE_SECRET": "video-dispatch-secret-value-0123456789abcdef",
            "VIDEO_SERVICE_CALLBACK_SECRET": "video-callback-secret-value-0123456789abcdef",
            "VIDEO_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.internal.video_processing.handle_callback",
            "MODERATION_ENABLED": "true",
            "MODERATION_SERVICE_URL": "http://127.0.0.1:8140",
            "MODERATION_SERVICE_SECRET": "moderation-dispatch-secret-value-0123456789abcdef",
            "MODERATION_SERVICE_CALLBACK_SECRET": "moderation-callback-secret-value-0123456789abcdef",
            "MODERATION_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.v1.moderation.handle_callback",
            "SEARCH_RANKING_ENABLED": "true",
            "SEARCH_RANKING_SERVICE_URL": "http://127.0.0.1:8150",
            "SEARCH_RANKING_SERVICE_SECRET": "search-dispatch-secret-value-0123456789abcdef",
            "SEARCH_RANKING_SERVICE_CALLBACK_SECRET": "search-callback-secret-value-0123456789abcdef",
            "SEARCH_RANKING_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.v1.search_ranking.handle_callback",
            "ANALYTICS_PIPELINE_ENABLED": "true",
            "ANALYTICS_SERVICE_URL": "http://127.0.0.1:8170",
            "ANALYTICS_SERVICE_SECRET": "analytics-dispatch-secret-value-0123456789abcdef",
            "ANALYTICS_SERVICE_CALLBACK_SECRET": "analytics-callback-secret-value-0123456789abcdef",
            "ANALYTICS_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.v1.analytics_pipeline.handle_callback",
            "NOTIFICATION_DELIVERY_ENABLED": "true",
            "NOTIFICATION_DRY_RUN": "false",
            "NOTIFICATION_SERVICE_URL": "http://127.0.0.1:8160",
            "NOTIFICATION_SERVICE_SECRET": "notification-dispatch-secret-value-0123456789abcdef",
            "NOTIFICATION_SERVICE_CALLBACK_SECRET": "notification-callback-secret-value-0123456789abcdef",
            "NOTIFICATION_CALLBACK_URL": "https://api.africaonlinestores.example-prod.com/api/method/aos.api.v1.notifications.handle_delivery_callback",
            "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH": str(self._firebase_path),
            "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH": str(self._firebase_path),
            "TRANSLATION_SERVICE_URL": "http://127.0.0.1:8100",
            "IMAGE_SEARCH_SERVICE_URL": "http://127.0.0.1:8110",
            "BACKGROUND_REMOVAL_SERVICE_URL": "http://127.0.0.1:8120",
            "BACKGROUND_REMOVAL_SERVICE_SECRET": "background-removal-secret-value-0123456789abcdef",
            "IMAGE_SEARCH_QDRANT_URL": "http://qdrant:6333",
            "SHORT_CLASSIFICATION_SECRET": "short-classification-secret-value-0123456789abcdef",
            "IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS": "files.africaonlinestores.example-prod.com",
            "MAPS_PUBLIC_BASE_URL": "https://maps.africaonlinestores.example-prod.com/basemap",
        }

    def _valid_site_config(self) -> dict[str, str]:
        return {
            "maps_photon_enabled": True,
            "photon_base_url": "http://photon:2322",
            "maps_nominatim_fallback_enabled": False,
            "maps_routing_enabled": True,
            "valhalla_base_url": "http://valhalla:8002",
        }

    def _healthy_get(self, url: str, timeout: int = 3):
        payload = {"ok": True, "ready": True, "service": "fake-service", "environment": "test"}
        return _FakeResponse(200, payload)

    def _report_text(self, report: dict) -> str:
        return str(report)

    def test_operational_health_all_services_ready_and_redacted(self):
        env = self._valid_env()
        with (
            patch("aos.utils.operational_health.frappe.cache", return_value=_FakeCache()),
            patch("aos.utils.operational_health.os.path.exists", return_value=True),
        ):
            report = validate_operational_health(
                env=env,
                site_config=self._valid_site_config(),
                http_get=self._healthy_get,
                storage_factory=_FakeStorage,
            )

        self.assertTrue(report.get("ready"), report)
        names = {check.get("name") for check in report.get("checks", [])}
        self.assertIn("production_config", names)
        self.assertIn("object_storage", names)
        self.assertIn("frappe_redis_cache", names)
        self.assertIn("firebase_credentials", names)
        self.assertIn("livekit_root", names)
        self.assertIn("video_processing_health", names)
        self.assertIn("video_processing_ready", names)
        self.assertIn("basemap_origin_current.json", names)
        self.assertIn("photon_status", names)
        self.assertIn("valhalla_status", names)

        serialized = self._report_text(report)
        for secret in [
            env["MINIO_ROOT_PASSWORD"],
            env["AOS_OBJECT_STORAGE_SECRET_KEY"],
            env["BACKGROUND_REMOVAL_SERVICE_SECRET"],
            env["LIVEKIT_API_SECRET"],
            env["VIDEO_SERVICE_SECRET"],
            env["NOTIFICATION_SERVICE_CALLBACK_SECRET"],
        ]:
            self.assertNotIn(secret, serialized)
        self.assertNotIn("firebase-service-account", serialized)

    def test_operational_health_marks_unreachable_service_unhealthy_without_leaking_exception(self):
        env = self._valid_env()

        def failing_get(url: str, timeout: int = 3):
            if ":8130" in url:
                raise RuntimeError("token=super-secret-value should-not-leak")
            return self._healthy_get(url, timeout=timeout)

        with (
            patch("aos.utils.operational_health.frappe.cache", return_value=_FakeCache()),
            patch("aos.utils.operational_health.os.path.exists", return_value=True),
        ):
            report = validate_operational_health(
                env=env,
                site_config=self._valid_site_config(),
                http_get=failing_get,
                storage_factory=_FakeStorage,
            )

        self.assertFalse(report.get("ready"), report)
        video_checks = [
            check for check in report.get("checks", [])
            if str(check.get("name", "")).startswith("video_processing")
        ]
        self.assertTrue(video_checks)
        self.assertIn("unhealthy", {check.get("status") for check in video_checks})
        serialized = self._report_text(report)
        self.assertNotIn("super-secret-value", serialized)
        self.assertNotIn("should-not-leak", serialized)

    def test_image_search_ready_failure_makes_marketplace_discovery_unready(self):
        env = self._valid_env()

        def image_search_ready_fails(url: str, timeout: int = 3):
            if ":8110/ready" in url:
                raise RuntimeError("internal vector store detail should not leak")
            return self._healthy_get(url, timeout=timeout)

        with (
            patch("aos.utils.operational_health.frappe.cache", return_value=_FakeCache()),
            patch("aos.utils.operational_health.os.path.exists", return_value=True),
        ):
            report = validate_operational_health(
                env=env,
                site_config=self._valid_site_config(),
                http_get=image_search_ready_fails,
                storage_factory=_FakeStorage,
            )

        self.assertFalse(report.get("ready"), report)
        image_ready = [
            check for check in report.get("checks", [])
            if check.get("name") == "image_search_ready"
        ]
        self.assertEqual(len(image_ready), 1)
        self.assertEqual(image_ready[0].get("status"), "unhealthy")
        serialized = self._report_text(report)
        self.assertNotIn("vector store detail", serialized)

    def test_disabled_optional_service_is_skipped(self):
        env = self._valid_env()
        env["MODERATION_ENABLED"] = "false"
        with (
            patch("aos.utils.operational_health.frappe.cache", return_value=_FakeCache()),
            patch("aos.utils.operational_health.os.path.exists", return_value=True),
        ):
            report = validate_operational_health(
                env=env,
                site_config=self._valid_site_config(),
                http_get=self._healthy_get,
                storage_factory=_FakeStorage,
            )

        moderation = [check for check in report.get("checks", []) if check.get("name") == "moderation"]
        self.assertEqual(len(moderation), 1)
        self.assertEqual(moderation[0].get("status"), "skipped")

    def test_admin_diagnostic_requires_system_manager(self):
        frappe.set_user("Guest")
        response = get_operational_health_status()
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "PERMISSION_DENIED")

    def test_admin_diagnostic_returns_redacted_report_for_system_manager(self):
        frappe.set_user("Administrator")
        expected = {"ready": True, "summary": {"checks": 1, "healthy": 1, "degraded": 0, "unhealthy": 0, "skipped": 0}, "checks": []}
        with patch("aos.api.diagnostics.status.validate_operational_health", return_value=expected):
            response = get_operational_health_status()
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data"), expected)
