from __future__ import annotations


import frappe
from frappe.tests.utils import FrappeTestCase

from aos.utils.job_monitoring import validate_job_monitoring


class TestJobMonitoring(FrappeTestCase):
    """Focused tests for production job monitoring diagnostics."""

    def setUp(self):
        frappe.local.response = {}

    def _validate(self, **kwargs):
        kwargs.setdefault("worker_stats_provider", lambda: {"worker_count": 2, "queues": ["short", "default", "long"]})
        kwargs.setdefault("scheduler_stats_provider", lambda: {"active": True})
        return validate_job_monitoring(**kwargs)

    def _healthy_service_stats(self, **overrides):
        base = {
            "name": "video_processing_jobs",
            "doctype": "AOS Video Processing Job",
            "category": "video_processing",
            "enabled": True,
            "counts_by_status": {"Ready": 3, "Queued": 0},
            "active_count": 0,
            "failed_count": 0,
            "stale_active_count": 0,
            "long_running_count": 0,
            "retry_risk_count": 0,
            "stale_threshold_minutes": 30,
            "long_running_threshold_minutes": 30,
            "sample_failed_jobs": [],
            "sample_stale_jobs": [],
        }
        base.update(overrides)
        return base

    def _healthy_queue_stats(self, **overrides):
        base = {
            "queue": "long",
            "queued_count": 0,
            "failed_count": 0,
            "started_count": 0,
            "scheduled_count": 0,
            "deferred_count": 0,
        }
        base.update(overrides)
        return base

    def _healthy_outbox_summary(self):
        return {
            "queue_depth": 0,
            "claimed_count": 0,
            "stale_lease_count": 0,
            "oldest_queued_age_seconds": 0,
            "dead_letter_count": 0,
            "manual_review_count": 0,
            "by_service": {},
        }

    def test_job_monitoring_all_clear_and_redacted(self):
        secret_error = "token=super-secret-value traceback should not leak"

        def service_provider(**kwargs):
            return [
                self._healthy_service_stats(),
                self._healthy_service_stats(
                    name="notification_delivery_jobs",
                    doctype="AOS Notification Delivery Job",
                    category="notification_delivery",
                    counts_by_status={"Delivered": 10},
                ),
            ]

        def queue_provider(**kwargs):
            return [self._healthy_queue_stats(queue="short"), self._healthy_queue_stats(queue="long")]

        def background_error_provider(**kwargs):
            return {"error_count": 0, "window_hours": 24, "sample_methods": [secret_error]}

        report = self._validate(
            service_job_stats_provider=service_provider,
            queue_stats_provider=queue_provider,
            background_error_provider=background_error_provider,
            outbox_summary_provider=self._healthy_outbox_summary,
        )

        self.assertTrue(report.get("ready"), report)
        names = {check.get("name") for check in report.get("checks", [])}
        self.assertIn("video_processing_jobs", names)
        self.assertIn("notification_delivery_jobs", names)
        self.assertIn("frappe_queue:short", names)
        self.assertIn("frappe_background_job_errors", names)
        serialized = str(report)
        self.assertNotIn("super-secret-value", serialized)
        self.assertNotIn("traceback should not leak", serialized)

    def test_stale_service_jobs_make_report_unready_without_leaking_error_payloads(self):
        def service_provider(**kwargs):
            return [
                self._healthy_service_stats(
                    active_count=2,
                    stale_active_count=1,
                    long_running_count=1,
                    counts_by_status={"Processing": 2},
                    sample_stale_jobs=["a1b2c3d4e5"],
                    last_error="AccessDenied secret=should-not-leak",
                )
            ]

        report = self._validate(
            service_job_stats_provider=service_provider,
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats()],
            background_error_provider=lambda **kwargs: {"error_count": 0, "window_hours": 24, "sample_methods": []},
            outbox_summary_provider=self._healthy_outbox_summary,
        )

        self.assertFalse(report.get("ready"), report)
        service_checks = [check for check in report.get("checks", []) if check.get("name") == "video_processing_jobs"]
        self.assertEqual(len(service_checks), 1)
        self.assertEqual(service_checks[0].get("status"), "unhealthy")
        serialized = str(report)
        self.assertIn("a1b2c3d4e5", serialized)
        self.assertNotIn("AccessDenied", serialized)
        self.assertNotIn("should-not-leak", serialized)

    def test_queue_backlog_and_failed_background_jobs_are_degraded_not_secret_leaking(self):
        def queue_provider(**kwargs):
            return [self._healthy_queue_stats(queue="long", queued_count=1200, failed_count=2)]

        def background_error_provider(**kwargs):
            return {
                "error_count": 2,
                "window_hours": 24,
                "sample_methods": ["aos.tasks.video_processing.dispatch_video_processing_job"],
            }

        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats()],
            queue_stats_provider=queue_provider,
            background_error_provider=background_error_provider,
            queue_backlog_warning=1000,
            queue_backlog_unhealthy=10000,
            outbox_summary_provider=self._healthy_outbox_summary,
        )

        self.assertTrue(report.get("ready"), report)
        statuses = {check.get("name"): check.get("status") for check in report.get("checks", [])}
        self.assertEqual(statuses.get("frappe_queue:long"), "degraded")
        self.assertEqual(statuses.get("frappe_background_job_errors"), "degraded")
        serialized = str(report)
        self.assertIn("aos.tasks.video_processing.dispatch_video_processing_job", serialized)
        self.assertNotIn("Traceback", serialized)

    def test_queue_unhealthy_makes_report_unready(self):
        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats()],
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats(queue="long", queued_count=10001)],
            background_error_provider=lambda **kwargs: {"error_count": 0, "window_hours": 24, "sample_methods": []},
            queue_backlog_warning=1000,
            queue_backlog_unhealthy=10000,
            outbox_summary_provider=self._healthy_outbox_summary,
        )

        self.assertFalse(report.get("ready"), report)
        queue = [check for check in report.get("checks", []) if check.get("name") == "frappe_queue:long"]
        self.assertEqual(queue[0].get("status"), "unhealthy")

    def test_outbox_manual_review_makes_report_unready(self):
        def outbox_summary():
            summary = self._healthy_outbox_summary()
            summary["manual_review_count"] = 1
            return summary

        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats()],
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats()],
            background_error_provider=lambda **kwargs: {
                "error_count": 0,
                "window_hours": 24,
                "sample_methods": [],
            },
            outbox_summary_provider=outbox_summary,
        )

        self.assertFalse(report.get("ready"), report)
        outbox = next(
            check for check in report.get("checks", []) if check.get("name") == "transactional_outbox"
        )
        self.assertEqual(outbox.get("status"), "unhealthy")
        self.assertEqual((outbox.get("details") or {}).get("manual_review_count"), 1)

    def test_disabled_service_is_neutral_and_explicit(self):
        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats(enabled=False)],
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats()],
            background_error_provider=lambda **kwargs: {"error_count": 0, "window_hours": 24, "sample_methods": []},
            outbox_summary_provider=self._healthy_outbox_summary,
        )
        service = next(check for check in report["checks"] if check["name"] == "video_processing_jobs")
        self.assertEqual(service["status"], "disabled")
        self.assertEqual(service["requirement"], "optional")
        self.assertTrue(report["ready"])


    def test_inspection_failure_is_unknown_and_blocks_job_health(self):
        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [
                {
                    "name": "video_processing_jobs",
                    "doctype": "AOS Video Processing Job",
                    "category": "video_processing",
                    "inspection_failed": True,
                }
            ],
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats()],
            background_error_provider=lambda **kwargs: {
                "error_count": 0,
                "window_hours": 24,
                "sample_methods": [],
            },
            outbox_summary_provider=self._healthy_outbox_summary,
        )
        service = next(check for check in report["checks"] if check["name"] == "video_processing_jobs")
        self.assertEqual(service["status"], "unknown")
        self.assertEqual(service["condition"], "unknown")
        self.assertFalse(report["ready"])

    def test_queue_registry_inspection_failure_is_unknown_and_blocks_job_health(self):
        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats()],
            queue_stats_provider=lambda **kwargs: [
                self._healthy_queue_stats(queue="long", inspection_failed=True)
            ],
            background_error_provider=lambda **kwargs: {
                "error_count": 0,
                "window_hours": 24,
                "sample_methods": [],
            },
            outbox_summary_provider=self._healthy_outbox_summary,
        )
        queue = next(check for check in report["checks"] if check["name"] == "frappe_queue:long")
        self.assertEqual(queue["status"], "unknown")
        self.assertEqual(queue["condition"], "unknown")
        self.assertFalse(report["ready"])

    def test_background_error_inspection_failure_is_unknown_and_blocks_job_health(self):
        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats()],
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats()],
            background_error_provider=lambda **kwargs: {
                "inspection_failed": True,
                "window_hours": 24,
                "sample_methods": [],
            },
            outbox_summary_provider=self._healthy_outbox_summary,
        )
        check = next(
            check for check in report["checks"] if check["name"] == "frappe_background_job_errors"
        )
        self.assertEqual(check["status"], "unknown")
        self.assertEqual(check["condition"], "unknown")
        self.assertFalse(report["ready"])

    def test_missing_workers_or_scheduler_blocks_job_health(self):
        report = self._validate(
            service_job_stats_provider=lambda **kwargs: [self._healthy_service_stats()],
            queue_stats_provider=lambda **kwargs: [self._healthy_queue_stats()],
            background_error_provider=lambda **kwargs: {"error_count": 0, "window_hours": 24, "sample_methods": []},
            outbox_summary_provider=self._healthy_outbox_summary,
            worker_stats_provider=lambda: {"worker_count": 0, "queues": []},
            scheduler_stats_provider=lambda: {"active": False},
        )
        self.assertFalse(report["ready"])
        statuses = {check["name"]: check["status"] for check in report["checks"]}
        self.assertEqual(statuses["frappe_workers"], "unhealthy")
        self.assertEqual(statuses["frappe_scheduler"], "unhealthy")

