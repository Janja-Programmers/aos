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

	def test_outbox_dispatch_keeps_stable_rq_job_ids(self):
		source = Path(frappe.get_app_path("aos", "services", "transactional_outbox.py")).read_text(
			encoding="utf-8"
		)
		self.assertIn('"rq_job_id": f"outbox:{outbox_key}"', source)
		self.assertIn("row.rq_job_id or f'outbox:{row.idempotency_key}'", source)
		self.assertIn(":g{int(claim['attempt_count'])}", source)
		self.assertIn("FOR UPDATE SKIP LOCKED", source)

		service_expectations = {
			"video_processing_service.py": "video_processing",
			"analytics_pipeline_service.py": "analytics_ingestion",
			"moderation_service.py": "moderation",
			"notification_delivery_service.py": "notification_delivery",
			"search_ranking_service.py": "search_indexing",
		}
		for filename, service_type in service_expectations.items():
			with self.subTest(file=filename):
				service_path = Path(frappe.get_app_path("aos", "services", filename))
				service_source = service_path.read_text(encoding="utf-8")
				self.assertIn("ensure_outbox_for_job", service_source)
				self.assertIn(f'service_type="{service_type}"', service_source)

				tree = ast.parse(service_source, filename=str(service_path))
				direct_enqueue_lines = [
					node.lineno
					for node in ast.walk(tree)
					if isinstance(node, ast.Call)
					and isinstance(node.func, ast.Attribute)
					and node.func.attr == "enqueue"
					and isinstance(node.func.value, ast.Name)
					and node.func.value.id == "frappe"
				]
				self.assertEqual(
					direct_enqueue_lines,
					[],
					f"{filename} must dispatch through the transactional outbox, not frappe.enqueue",
				)
