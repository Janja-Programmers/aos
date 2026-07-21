from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.metrics import prometheus
from aos.utils import metrics


class TestOperationalMetrics(FrappeTestCase):
	def setUp(self):
		frappe.local.response = {}
		with metrics._LOCK:
			metrics._REQUESTS.clear()
			metrics._DURATIONS.clear()
			metrics._DURATION_SUM.clear()
			metrics._DURATION_BUCKETS.clear()
			metrics._EXCEPTIONS.clear()
			metrics._RATE_LIMIT_REJECTIONS.clear()

	def _outbox_summary(self):
		services = {
			service: {
				"created_total": 2,
				"dispatched_total": 3,
				"completed_total": 1,
				"failed_total": 1,
				"retried_total": 1,
				"dead_lettered_total": 0,
				"callback_timeouts_total": 1,
				"redispatch_accepted_total": 1,
				"redispatch_skipped_total": 0,
				"redispatch_failure_total": 1,
				"duplicate_active_dispatch_total": 1,
				"callback_replay_total": 1,
				"old_generation_rejection_total": 1,
				"token_mismatch_total": 1,
				"transaction_rollback_total": 1,
				"duration_seconds_sum": 1.25,
				"duration_seconds_count": 1,
			}
			for service in (
				"video_processing",
				"moderation",
				"search_indexing",
				"notification_delivery",
				"analytics_ingestion",
			)
		}
		return {
			"queue_depth": 2,
			"claimed_count": 1,
			"stale_lease_count": 0,
			"callback_overdue_count": 1,
			"oldest_queued_age_seconds": 30,
			"dead_letter_count": 0,
			"by_service": {service: {"Queued": 1} for service in services},
			"lifecycle_by_service": services,
		}

	def _render(self):
		health = {
			"checks": [
				{"name": "frappe_redis", "status": "healthy"},
				{"name": "database", "status": "healthy"},
				{"name": "minio", "status": "healthy"},
				{"name": "livekit_health", "status": "healthy"},
			]
		}
		backup = {
			"ready": True,
			"checks": [
				{
					"name": "latest_backup_artifact",
					"status": "healthy",
					"details": {"latest_backup_age_hours": 1},
				},
				{"name": "offsite_backup_scope", "status": "healthy", "details": {}},
				{"name": "backup_encryption", "status": "healthy", "details": {}},
				{
					"name": "restore_rehearsal",
					"status": "healthy",
					"details": {"restore_rehearsal_age_days": 1},
				},
			],
		}
		config = {"ready": True, "summary": {"errors": 0}}
		with patch(
			"aos.services.transactional_outbox.outbox_monitoring_summary", return_value=self._outbox_summary()
		):
			with patch("aos.utils.operational_health.validate_operational_health", return_value=health):
				with patch("aos.utils.backup_readiness.validate_backup_readiness", return_value=backup):
					with patch("aos.utils.production_config.validate_production_config", return_value=config):
						return metrics.render_metrics()

	def test_metric_families_exist_with_low_cardinality_labels(self):
		metrics.record_rate_limit_rejection("sensitive")
		metrics.on_error()
		output = self._render()
		required = {
			"aos_http_requests_total",
			"aos_http_request_duration_seconds",
			"aos_unhandled_exceptions_total",
			"aos_rate_limit_rejections_total",
			"aos_background_jobs_created_total",
			"aos_background_jobs_dispatched_total",
			"aos_background_jobs_completed_total",
			"aos_background_jobs_failed_total",
			"aos_background_jobs_retried_total",
			"aos_background_jobs_dead_lettered_total",
			"aos_background_callback_timeouts_total",
			"aos_background_redispatch_accepted_total",
			"aos_background_redispatch_skipped_total",
			"aos_background_redispatch_failures_total",
			"aos_background_duplicate_active_dispatch_total",
			"aos_background_callback_replay_total",
			"aos_background_old_generation_callback_rejections_total",
			"aos_background_callback_token_mismatches_total",
			"aos_background_callback_transaction_rollbacks_total",
			"aos_background_queue_depth",
			"aos_background_oldest_queued_job_age_seconds",
			"aos_background_claimed_jobs",
			"aos_background_stale_leases",
			"aos_background_callback_overdue",
			"aos_background_job_duration_seconds",
			"aos_dependency_ready",
			"aos_backup_age_seconds",
			"aos_backup_verification_status",
			"aos_backup_offsite_status",
			"aos_backup_encryption_status",
			"aos_restore_rehearsal_age_seconds",
			"aos_production_readiness_status",
			"aos_missing_required_configuration",
			"aos_disk_free_bytes",
		}
		for name in required:
			self.assertIn(name, output)
		allowed_label_names = {
			"method",
			"surface",
			"status_class",
			"category",
			"policy",
			"service",
			"state",
			"dependency",
			"le",
		}
		for line in output.splitlines():
			if "{" not in line or line.startswith("#"):
				continue
			label_block = line.split("{", 1)[1].split("}", 1)[0]
			for pair in label_block.split(","):
				self.assertIn(pair.split("=", 1)[0], allowed_label_names)

	def test_metrics_never_include_sensitive_values_or_dynamic_ids(self):
		secret = "super-secret-token@example.com"
		with metrics._LOCK:
			metrics._REQUESTS[("GET", "api", "2xx")] = 1
		output = self._render()
		self.assertNotIn(secret, output)
		self.assertNotIn("user@example.com", output)
		self.assertNotIn("SHORT-2026-00123", output)
		self.assertNotIn("/api/method/", output)
		self.assertNotIn("Authorization", output)

	def test_metrics_endpoint_requires_token_or_loopback(self):
		frappe.local.request_ip = "203.0.113.9"
		with patch.dict(
			"os.environ",
			{"AOS_METRICS_TOKEN": "strong-metrics-token-0123456789", "AOS_METRICS_ALLOW_LOOPBACK": "false"},
			clear=False,
		):
			with patch("aos.utils.metrics.frappe.get_request_header", return_value=None):
				denied = prometheus()
		self.assertEqual(denied["error"], "PERMISSION_DENIED")
		self.assertEqual(frappe.local.response.get("http_status_code"), 403)

	def test_metrics_endpoint_returns_openmetrics_for_valid_token(self):
		frappe.local.request_ip = "203.0.113.9"
		token = "strong-metrics-token-0123456789"
		with patch.dict(
			"os.environ", {"AOS_METRICS_TOKEN": token, "AOS_METRICS_ALLOW_LOOPBACK": "false"}, clear=False
		):
			with patch("aos.utils.metrics.frappe.get_request_header", return_value=f"Bearer {token}"):
				with patch("aos.api.metrics.render_metrics", return_value="# HELP test metric\ntest 1\n"):
					response = prometheus()
		self.assertIsNone(response)
		self.assertEqual(frappe.local.response.get("type"), "binary")
		self.assertIn("application/openmetrics-text", frappe.local.response.get("content_type", ""))
		self.assertNotIn(token, str(frappe.local.response))

	def test_redis_aggregation_is_consistent_across_simulated_workers(self):
		class FakeRedis:
			def __init__(self): self.hashes = {}
			def hincrby(self, key, field, amount):
				self.hashes.setdefault(key, {})[field] = int(self.hashes.setdefault(key, {}).get(field, 0)) + int(amount)
			def hincrbyfloat(self, key, field, amount):
				self.hashes.setdefault(key, {})[field] = float(self.hashes.setdefault(key, {}).get(field, 0)) + float(amount)
			def hgetall(self, key): return dict(self.hashes.get(key, {}))

		redis = FakeRedis()
		with patch.dict("os.environ", {"AOS_ENVIRONMENT": "production", "AOS_METRICS_ALLOW_PROCESS_FALLBACK": "false"}, clear=False):
			with patch("aos.utils.metrics._redis_cache", return_value=redis):
				for _worker in range(2):
					frappe.local.request = SimpleNamespace(path="/api/method/aos.test", method="GET")
					frappe.local.response = {"http_status_code": 200}
					metrics.before_request()
					metrics.after_request()
					with metrics._LOCK:
						metrics._REQUESTS.clear()  # simulate another worker process
				requests, counts, _sums, _buckets, _errors, _limits, ready = metrics._http_snapshot()
		self.assertEqual(ready, 1)
		self.assertEqual(requests[("GET", "api", "2xx")], 2)
		self.assertEqual(counts[("GET", "api")], 2)

	def test_production_does_not_fall_back_to_process_local_counters(self):
		with metrics._LOCK:
			metrics._REQUESTS[("GET", "api", "2xx")] = 99
		with patch.dict("os.environ", {"AOS_ENVIRONMENT": "production"}, clear=False):
			with patch("aos.utils.metrics._redis_cache", side_effect=RuntimeError("redis unavailable")):
				requests, _counts, _sums, _buckets, _errors, _limits, ready = metrics._http_snapshot()
		self.assertEqual(ready, 0)
		self.assertEqual(requests, {})
