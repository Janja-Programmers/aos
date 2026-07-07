from __future__ import annotations

import ast
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase


class TestEnqueueJobIds(FrappeTestCase):
    """Ensure Frappe enqueue calls use the supported Redis job_id keyword.

    Frappe v17 deprecates job_name= in favor of job_id=. These checks are
    intentionally source-based so the full test suite catches regressions
    without requiring a Redis worker or real background queue.
    """

    def _python_files(self):
        app_path = Path(frappe.get_app_path("aos")).parent
        roots = [app_path / "aos", app_path / "docs"]
        for root in roots:
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                if "__pycache__" in path.parts:
                    continue
                yield path

    def test_frappe_enqueue_calls_do_not_use_deprecated_job_name(self):
        offenders: list[str] = []

        for path in self._python_files():
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                is_frappe_enqueue = (
                    isinstance(func, ast.Attribute)
                    and func.attr == "enqueue"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "frappe"
                )
                if not is_frappe_enqueue:
                    continue

                for keyword in node.keywords:
                    if keyword.arg == "job_name":
                        offenders.append(f"{path.relative_to(path.parents[1])}:{node.lineno}")

        self.assertEqual(offenders, [], "Replace frappe.enqueue(job_name=...) with job_id=...")

    def test_dispatch_enqueue_calls_keep_stable_job_ids(self):
        source = Path(
            frappe.get_app_path("aos", "services", "video_processing_service.py")
        ).read_text()
        self.assertIn('job_id=f"dispatch-video-processing:{job_id}"', source)
        self.assertIn("video_job_id=job_id", source)

        service_expectations = {
            "analytics_pipeline_service.py": "dispatch-analytics-ingest:",
            "moderation_service.py": "dispatch-moderation:",
            "notification_delivery_service.py": "dispatch-notification-delivery:",
            "search_ranking_service.py": "dispatch-search-index:",
            "seller_response_metrics.py": "seller-response-metrics:",
        }

        for filename, prefix in service_expectations.items():
            with self.subTest(file=filename):
                service_source = Path(
                    frappe.get_app_path("aos", "services", filename)
                ).read_text()
                self.assertIn("job_id=", service_source)
                self.assertIn(prefix, service_source)
                self.assertNotIn("job_name=", service_source)

        call_source = Path(frappe.get_app_path("aos", "api", "calls", "call.py")).read_text()
        self.assertIn('job_id=f"aos_call_timeout:{call_id}"', call_source)
        self.assertIn("call_id=call_id", call_source)
        self.assertNotIn("job_name=", call_source)
