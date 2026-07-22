from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.analytics_pipeline_service import (
    dispatch_analytics_ingest_job,
    handle_analytics_ingest_callback,
)


class TestAnalyticsPipelineStaleLinks(FrappeTestCase):
    """Analytics jobs should survive historical links being deleted later."""

    def setUp(self):
        self.prefix = frappe.generate_hash(length=10)
        frappe.set_user("Administrator")

    def tearDown(self):
        frappe.db.sql("DELETE FROM `tabAOS Analytics Ingest Job` WHERE idempotency_key LIKE %s", (f"{self.prefix}%",))
        frappe.set_user("Administrator")
        frappe.db.commit()

    def _make_stale_link_job(self):
        job = frappe.get_doc(
            {
                "doctype": "AOS Analytics Ingest Job",
                "source": "test",
                "event_group": "shorts",
                "event_type": "view",
                "user": f"{self.prefix}-missing-user@example.com",
                "target_doctype": "AOS Short",
                "target_name": f"{self.prefix}-missing-short",
                "status": "Queued",
                "attempt_count": 0,
                "max_attempts": 3,
                "idempotency_key": f"{self.prefix}-stale-links",
                "event_count": 1,
                "events_json": "[]",
            }
        )
        job.flags.ignore_links = True
        job.insert(ignore_permissions=True)
        frappe.db.commit()
        return job

    def test_dispatch_ignores_stale_historical_link_fields(self):
        job = self._make_stale_link_job()

        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"service_job_id": f"svc-{self.prefix}", "dispatch_action": "enqueued"},
        )

        with patch("aos.services.analytics_pipeline_service.requests.post", return_value=response):
            dispatched = dispatch_analytics_ingest_job(job.name)

        self.assertEqual(dispatched.status, "Processing")
        self.assertEqual(frappe.db.get_value("AOS Analytics Ingest Job", job.name, "status"), "Processing")
        self.assertEqual(
            frappe.db.get_value("AOS Analytics Ingest Job", job.name, "service_job_id"),
            f"svc-{self.prefix}",
        )

    def test_callback_ignores_stale_historical_link_fields(self):
        job = self._make_stale_link_job()
        frappe.db.set_value("AOS Analytics Ingest Job", job.name, "status", "Processing", update_modified=False)
        frappe.db.commit()

        updated = handle_analytics_ingest_callback(
            {
                "job_id": job.name,
                "status": "ingested",
                "ingested_count": 1,
                "skipped_count": 0,
                "counters": {"short_views": 1},
            }
        )

        self.assertEqual(updated.status, "Ingested")
        self.assertEqual(frappe.db.get_value("AOS Analytics Ingest Job", job.name, "status"), "Ingested")
