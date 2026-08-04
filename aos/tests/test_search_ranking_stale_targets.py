from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.search_ranking_service import (
	SearchRankingConfig,
	_prepare_missing_target_delete,
	create_search_index_job,
)


class TestSearchRankingStaleTargets(FrappeTestCase):
	def _config(self) -> SearchRankingConfig:
		return SearchRankingConfig(
			service_url="http://127.0.0.1:8150",
			service_secret="test-secret",
			callback_secret="test-callback-secret",
			callback_url="http://127.0.0.1/callback",
			request_timeout_seconds=20,
			max_attempts=3,
			queue="long",
			dispatcher_timeout_seconds=300,
			enabled=True,
			fail_open=True,
			use_ads_search=True,
			use_shorts_feed=True,
		)

	def test_missing_target_delete_job_and_outbox_are_persistable(self):
		savepoint = f"search_stale_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			with (
				patch("aos.services.search_ranking_service.get_search_ranking_config", return_value=self._config()),
				patch("aos.services.transactional_outbox.register_after_commit_publish"),
			):
				job = create_search_index_job(
					target_doctype="AOS Short",
					target_name="SHORT-2099-MISSING",
					index_kind="short",
					action="delete",
					source="test_missing_target",
					document={},
					enqueue=True,
				)

			self.assertIsNotNone(job)
			self.assertEqual(job.action, "delete")
			outbox_name = frappe.db.get_value(
				"AOS Transactional Outbox",
				{"job_doctype": "AOS Search Index Job", "job_name": job.name},
				"name",
			)
			self.assertTrue(outbox_name)
			outbox = frappe.get_doc("AOS Transactional Outbox", outbox_name)
			self.assertFalse(outbox.aggregate_doctype)
			self.assertFalse(outbox.aggregate_name)
		finally:
			frappe.db.rollback(save_point=savepoint)

	def test_undispatched_stale_upsert_is_converted_to_delete(self):
		job = SimpleNamespace(
			target_doctype="AOS Short",
			target_name="SHORT-2099-MISSING",
			action="upsert",
			service_job_id=None,
			attempt_count=0,
			document_json='{"caption":"stale"}',
			indexed=1,
			last_error="old",
			flags=SimpleNamespace(ignore_links=False),
		)
		with patch("aos.services.search_ranking_service.frappe.db.exists", return_value=False):
			state = _prepare_missing_target_delete(job)
		self.assertEqual(state, "converted")
		self.assertEqual(job.action, "delete")
		self.assertEqual(job.document_json, "{}")
		self.assertEqual(job.indexed, 0)
		self.assertTrue(job.flags.ignore_links)

	def test_accepted_stale_upsert_requires_fresh_delete_correlation(self):
		job = SimpleNamespace(
			target_doctype="AOS Short",
			target_name="SHORT-2099-MISSING",
			action="upsert",
			service_job_id="stable-service-job",
			attempt_count=1,
			document_json='{"caption":"stale"}',
			indexed=1,
			last_error=None,
			flags=SimpleNamespace(ignore_links=False),
		)
		with patch("aos.services.search_ranking_service.frappe.db.exists", return_value=False):
			state = _prepare_missing_target_delete(job)
		self.assertEqual(state, "replacement_required")
		self.assertEqual(job.action, "upsert")
		self.assertTrue(job.flags.ignore_links)
