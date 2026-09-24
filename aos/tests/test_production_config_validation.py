from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.v1.diagnostics import get_production_config_status
from aos.utils.production_config import (
	ProductionConfigError,
	assert_production_config_ready,
	report_contains_secret_value,
	validate_production_config,
	validate_restore_rehearsal_config,
	validate_staging_config,
)


class TestProductionConfigValidation(FrappeTestCase):
	"""Focused tests for production-readiness config diagnostics."""

	def setUp(self):
		frappe.local.response = {}

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
			"LIVEKIT_ADMIN_ENDPOINT": "http://127.0.0.1:7880",
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
			"NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH": "/run/secrets/firebase-service-account.json",
			"TRANSLATION_SERVICE_URL": "http://127.0.0.1:8100",
			"TRANSLATION_INTERNAL_TOKEN": "translation-internal-secret-value-0123456789abcdef",
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

	def test_restore_rehearsal_config_is_non_production_but_equivalently_hardened(self):
		env = self._valid_env()
		env["AOS_ENVIRONMENT"] = "rehearsal"
		report = validate_restore_rehearsal_config(
			env=env,
			site_config=self._valid_site_config(),
		)
		self.assertTrue(report["ready"], report)

	def test_restore_rehearsal_rejects_production_environment_identity(self):
		report = validate_restore_rehearsal_config(
			env=self._valid_env(),
			site_config=self._valid_site_config(),
		)
		self.assertFalse(report["ready"])
		self.assertIn("AOS_ENVIRONMENT", {issue["key"] for issue in report["errors"]})

	def test_livekit_admin_endpoint_is_separate_from_public_signaling(self):
		env = self._valid_env()
		env["LIVEKIT_ADMIN_ENDPOINT"] = "https://live.africaonlinestores.example-prod.com"
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertFalse(report["ready"])
		self.assertIn("LIVEKIT_ADMIN_ENDPOINT", {issue["key"] for issue in report["errors"]})

	def test_livekit_admin_endpoint_rejects_websocket_scheme(self):
		env = self._valid_env()
		env["LIVEKIT_ADMIN_ENDPOINT"] = "ws://127.0.0.1:7880"
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertFalse(report["ready"])
		self.assertIn("LIVEKIT_ADMIN_ENDPOINT", {issue["key"] for issue in report["errors"]})

	def test_maps_production_config_requires_global_routing(self):
		site_config = self._valid_site_config()
		site_config["maps_routing_enabled"] = False
		report = validate_production_config(env=self._valid_env(), site_config=site_config)
		self.assertFalse(report["ready"])
		self.assertIn("maps_routing_enabled", {issue["key"] for issue in report["errors"]})

	def test_complete_staging_config_uses_production_equivalent_safety(self):
		env = self._valid_env()
		env["AOS_ENVIRONMENT"] = "staging"
		report = validate_staging_config(env=env, site_config=self._valid_site_config())
		self.assertTrue(report["ready"], report)

	def test_staging_config_rejects_wrong_environment_name(self):
		report = validate_staging_config(
			env=self._valid_env(),
			site_config=self._valid_site_config(),
		)
		self.assertFalse(report["ready"])
		self.assertIn("AOS_ENVIRONMENT", {issue["key"] for issue in report["errors"]})

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
		self.assertFalse(
			report_contains_secret_value(report, env["AOS_OBJECT_STORAGE_SECRET_KEY"]),
			"Media object-storage secrets must never appear in diagnostics.",
		)

	def test_translation_internal_token_is_required(self):
		env = self._valid_env()
		env.pop("TRANSLATION_INTERNAL_TOKEN")
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertFalse(report["ready"])
		self.assertIn("TRANSLATION_INTERNAL_TOKEN", {issue["key"] for issue in report["errors"]})

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

	def test_media_object_storage_requires_provider_neutral_production_configuration(self):
		env = self._valid_env()
		env["AOS_OBJECT_STORAGE_MANAGE_BUCKETS"] = "true"
		env["AOS_OBJECT_STORAGE_SECURE"] = "false"
		env["AOS_OBJECT_STORAGE_PATH_STYLE"] = "not-a-boolean"
		env["AOS_MEDIA_PUBLIC_BASE_URL"] = "http://localhost:9100/aos-public"
		env.pop("BACKGROUND_REMOVAL_SERVICE_SECRET")

		report = validate_production_config(env=env, site_config=self._valid_site_config())

		self.assertFalse(report["ready"])
		keys = {issue["key"] for issue in report["errors"]}
		self.assertIn("AOS_OBJECT_STORAGE_MANAGE_BUCKETS", keys)
		self.assertIn("AOS_OBJECT_STORAGE_SECURE", keys)
		self.assertIn("AOS_OBJECT_STORAGE_PATH_STYLE", keys)
		self.assertIn("AOS_MEDIA_PUBLIC_BASE_URL", keys)
		self.assertIn("BACKGROUND_REMOVAL_SERVICE_SECRET", keys)

	def test_image_search_internal_boundary_fails_closed_without_secret_or_trusted_hosts(self):
		env = self._valid_env()
		env.pop("SHORT_CLASSIFICATION_SECRET")
		env.pop("IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS")
		env.pop("AOS_MEDIA_PUBLIC_BASE_URL")

		report = validate_production_config(env=env, site_config=self._valid_site_config())

		self.assertFalse(report["ready"])
		keys = {issue["key"] for issue in report["errors"]}
		self.assertIn("SHORT_CLASSIFICATION_SECRET/IMAGE_SEARCH_INTERNAL_SECRET", keys)
		self.assertIn("IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS/IMAGE_SEARCH_FILE_BASE_URL/AOS_MEDIA_PUBLIC_BASE_URL", keys)

	def test_image_search_allowed_hosts_rejects_wildcards_and_urls(self):
		env = self._valid_env()
		env["IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS"] = "*.example.com,https://files.example.com/path"

		report = validate_production_config(env=env, site_config=self._valid_site_config())

		self.assertFalse(report["ready"])
		self.assertIn("IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS", {issue["key"] for issue in report["errors"]})

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

	def test_service_callback_observation_timeout_must_exceed_work_runtime_margin(self):
		env = self._valid_env()
		env["VIDEO_JOB_TIMEOUT_SECONDS"] = "1800"
		env["AOS_VIDEO_CALLBACK_TIMEOUT_SECONDS"] = "2100"

		report = validate_production_config(env=env, site_config=self._valid_site_config())

		self.assertFalse(report["ready"])
		matching = [
			issue
			for issue in report["errors"]
			if issue["key"] == "AOS_VIDEO_CALLBACK_TIMEOUT_SECONDS"
		]
		self.assertTrue(matching, report)
		self.assertIn("callback retry margin", matching[0]["message"])

	def test_service_specific_callback_observation_timeouts_are_accepted(self):
		env = self._valid_env()
		env.update(
			{
				"VIDEO_JOB_TIMEOUT_SECONDS": "1800",
				"AOS_VIDEO_CALLBACK_TIMEOUT_SECONDS": "2700",
				"MODERATION_JOB_TIMEOUT_SECONDS": "600",
				"AOS_MODERATION_CALLBACK_TIMEOUT_SECONDS": "1200",
				"SEARCH_RANKING_JOB_TIMEOUT_SECONDS": "600",
				"AOS_SEARCH_CALLBACK_TIMEOUT_SECONDS": "1200",
				"NOTIFICATION_JOB_TIMEOUT_SECONDS": "600",
				"AOS_NOTIFICATION_CALLBACK_TIMEOUT_SECONDS": "1200",
				"ANALYTICS_JOB_TIMEOUT_SECONDS": "600",
				"AOS_ANALYTICS_CALLBACK_TIMEOUT_SECONDS": "1200",
			}
		)

		report = validate_production_config(env=env, site_config=self._valid_site_config())

		self.assertTrue(report["ready"], report)

	def test_maps_site_config_is_required(self):
		report = validate_production_config(
			env=self._valid_env(),
			site_config={},
		)

		self.assertFalse(report["ready"])
		keys = {issue["key"] for issue in report["errors"]}
		self.assertIn("maps_photon_enabled", keys)
		self.assertIn("photon_base_url", keys)
		self.assertIn("maps_routing_enabled", keys)
		self.assertIn("valhalla_base_url", keys)

	def test_enabled_photon_accepts_per_host_loopback_sidecar(self):
		site_config = self._valid_site_config()
		site_config["photon_base_url"] = "http://127.0.0.1:2322"
		report = validate_production_config(env=self._valid_env(), site_config=site_config)
		self.assertTrue(report["ready"], report)

	def test_enabled_photon_accepts_dedicated_internal_service_url(self):
		site_config = self._valid_site_config()
		site_config["maps_photon_enabled"] = True
		site_config["photon_base_url"] = "http://photon:2322"
		report = validate_production_config(env=self._valid_env(), site_config=site_config)
		self.assertTrue(report["ready"], report)

	def test_production_backup_encryption_placeholder_is_rejected_and_redacted(self):
		env = self._valid_env()
		env["BACKUP_AGE_RECIPIENT"] = "age1example-change-me"
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertFalse(report["ready"])
		self.assertIn("BACKUP_AGE_RECIPIENT", {issue["key"] for issue in report["errors"]})
		self.assertNotIn("age1example-change-me", str(report))

	def test_enabled_notification_web_push_requires_complete_public_firebase_bootstrap(self):
		env = self._valid_env()
		env["NOTIFICATION_WEB_PUSH_ENABLED"] = "true"
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		keys = {issue["key"] for issue in report["errors"]}
		self.assertIn("NOTIFICATION_FIREBASE_WEB_API_KEY", keys)
		self.assertIn("NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY", keys)

	def test_enabled_notification_web_push_accepts_complete_public_firebase_bootstrap(self):
		env = self._valid_env()
		env.update(
			{
				"NOTIFICATION_WEB_PUSH_ENABLED": "true",
				"NOTIFICATION_FIREBASE_WEB_API_KEY": "AOSFirebasePublicApiKey0123456789abcdef",
				"NOTIFICATION_FIREBASE_WEB_PROJECT_ID": "aos-production-2026",
				"NOTIFICATION_FIREBASE_WEB_MESSAGING_SENDER_ID": "123456789012",
				"NOTIFICATION_FIREBASE_WEB_APP_ID": "1:123456789012:web:abcdef0123456789",
				"NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY": "B" + "a" * 86,
			}
		)
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertTrue(report["ready"], report)

	def test_example_environment_file_mount_is_rejected(self):
		env = self._valid_env()
		env["AOS_ENV_FILE_PATH"] = "/etc/aos/backup.env.example"
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertFalse(report["ready"])
		self.assertIn("AOS_ENV_FILE_PATH", {issue["key"] for issue in report["errors"]})

	def test_example_or_broad_firebase_credential_is_rejected(self):
		with tempfile.TemporaryDirectory() as directory:
			credential = Path(directory) / "firebase-service-account.json"
			credential.write_text(
				'{"project_id":"example-project","private_key":"dummy-private-key"}', encoding="utf-8"
			)
			credential.chmod(0o644)
			env = self._valid_env()
			env["NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH"] = str(credential)
			report = validate_production_config(env=env, site_config=self._valid_site_config())
		self.assertFalse(report["ready"])
		firebase_errors = [
			issue
			for issue in report["errors"]
			if issue["key"] == "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH"
		]
		self.assertGreaterEqual(len(firebase_errors), 1)
		self.assertNotIn("dummy-private-key", str(report))
		self.assertNotIn(str(credential), str(report))

	def test_alerting_metrics_and_failure_notification_fail_closed(self):
		env = self._valid_env()
		env["AOS_METRICS_TOKEN"] = "change-this-token"
		env["AOS_ALERTING_ENABLED"] = "false"
		env["BACKUP_FAILURE_NOTIFICATION_METHOD"] = "none"
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		keys = {issue["key"] for issue in report["errors"]}
		self.assertIn("AOS_METRICS_TOKEN", keys)
		self.assertIn("AOS_ALERTING_ENABLED", keys)
		self.assertIn("BACKUP_FAILURE_NOTIFICATION_METHOD", keys)
		self.assertNotIn("change-this-token", str(report))

	def test_cd_placeholder_and_missing_manifest_gate_are_rejected(self):
		env = self._valid_env()
		env.update(
			{
				"AOS_CD_ENABLED": "true",
				"AOS_RELEASE_MANIFEST_REQUIRED": "false",
				"DEPLOY_HOST": "example.com",
				"DEPLOY_USER": "change-this-user",
			}
		)
		report = validate_production_config(env=env, site_config=self._valid_site_config())
		keys = {issue["key"] for issue in report["errors"]}
		self.assertIn("AOS_RELEASE_MANIFEST_REQUIRED", keys)
		self.assertIn("DEPLOY_HOST", keys)
		self.assertIn("DEPLOY_USER", keys)
		self.assertNotIn("change-this-user", str(report))

	def test_assert_production_config_ready_raises_for_invalid_runtime_config(self):
		with patch(
			"aos.utils.production_config.validate_production_config",
			return_value={"ready": False, "summary": {"errors": 2}, "errors": [], "warnings": []},
		):
			with self.assertRaises(ProductionConfigError):
				assert_production_config_ready()

	def test_admin_diagnostic_requires_aos_settings_read_permission(self):
		with patch(
			"aos.api.diagnostics.status.frappe.session", type("Session", (), {"user": "guest@example.com"})()
		):
			with patch(
				"aos.api.diagnostics.status.has_doctype_permission",
				return_value=False,
			):
				response = get_production_config_status()

		self.assertFalse(response["ok"])
		self.assertEqual(response["error"], "PERMISSION_DENIED")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)

	def test_admin_diagnostic_returns_redacted_report_for_authorized_role(self):
		report = {"ready": True, "summary": {"errors": 0, "warnings": 0}, "errors": [], "warnings": []}
		with patch(
			"aos.api.diagnostics.status.frappe.session", type("Session", (), {"user": "admin@example.com"})()
		):
			with patch(
				"aos.api.diagnostics.status.has_doctype_permission",
				return_value=True,
			):
				with patch("aos.api.diagnostics.status.validate_production_config", return_value=report):
					response = get_production_config_status()

		self.assertTrue(response["ok"])
		self.assertEqual(response["data"], report)
		self.assertEqual(frappe.local.response.get("http_status_code"), 200)
