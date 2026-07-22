from __future__ import annotations

import ast
import inspect
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from aos.services.transactional_outbox import (
	DISPATCH_SPECS,
	OUTBOX_DOCTYPE,
	SERVICE_TYPES,
	OutboxConflictError,
	ensure_outbox_for_job,
	mark_outbox_callback,
	requeue_dead_letter_outbox,
	retry_delay_seconds,
	stable_idempotency_key,
	validate_callback_idempotency,
)


class TestTransactionalOutbox(FrappeTestCase):
	"""Transactional, lease, idempotency, and recovery contract tests."""

	def _analytics_job(self):
		return frappe.get_doc(
			{
				"doctype": "AOS Analytics Ingest Job",
				"naming_series": "ANLY-JOB-.YYYY.-.#####",
				"status": "Queued",
				"events_json": "[]",
				"attempt_count": 0,
				"max_attempts": 3,
				"idempotency_key": uuid.uuid4().hex,
			}
		).insert(ignore_permissions=True)

	def test_all_supported_job_categories_have_recovery_specs(self):
		self.assertEqual(set(DISPATCH_SPECS), set(SERVICE_TYPES))
		self.assertEqual(
			set(SERVICE_TYPES),
			{
				"video_processing",
				"moderation",
				"search_indexing",
				"notification_delivery",
				"analytics_ingestion",
			},
		)
		for spec in DISPATCH_SPECS.values():
			self.assertTrue(spec.dispatch_method.startswith("aos.tasks."))
			self.assertTrue(spec.job_doctype.startswith("AOS "))
			self.assertTrue(spec.kwarg_name.endswith("_id"))

	def test_stable_idempotency_key_is_repeatable_and_bounded(self):
		first = stable_idempotency_key("analytics_ingestion", "AOS Analytics Ingest Job", "ANLY-1")
		second = stable_idempotency_key("analytics_ingestion", "AOS Analytics Ingest Job", "ANLY-1")
		other = stable_idempotency_key("analytics_ingestion", "AOS Analytics Ingest Job", "ANLY-2")
		self.assertEqual(first, second)
		self.assertNotEqual(first, other)
		self.assertTrue(first.startswith("aos:analytics_ingestion:"))
		self.assertLessEqual(len(first), 100)

	def test_domain_job_and_outbox_share_outer_transaction_and_rollback(self):
		savepoint = f"outbox_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		with patch("aos.services.transactional_outbox.register_after_commit_publish"):
			job = self._analytics_job()
			outbox = ensure_outbox_for_job(
				service_type="analytics_ingestion",
				job=job,
				queue="short",
				timeout_seconds=300,
			)
		self.assertTrue(frappe.db.exists(job.doctype, job.name))
		self.assertTrue(frappe.db.exists(OUTBOX_DOCTYPE, outbox.name))

		frappe.db.rollback(save_point=savepoint)

		self.assertFalse(frappe.db.exists(job.doctype, job.name))
		self.assertFalse(frappe.db.exists(OUTBOX_DOCTYPE, outbox.name))

	def test_ensure_is_idempotent_for_the_same_durable_job(self):
		savepoint = f"outbox_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				job = self._analytics_job()
				first = ensure_outbox_for_job(
					service_type="analytics_ingestion", job=job, queue="short", timeout_seconds=300
				)
				second = ensure_outbox_for_job(
					service_type="analytics_ingestion", job=job, queue="short", timeout_seconds=300
				)
			self.assertEqual(first.name, second.name)
			self.assertEqual(first.idempotency_key, second.idempotency_key)
		finally:
			frappe.db.rollback(save_point=savepoint)

	def test_shared_enqueue_helpers_do_not_force_commit(self):
		service_files = {
			"video_processing_service.py": "enqueue_dispatch",
			"moderation_service.py": "enqueue_moderation_dispatch",
			"search_ranking_service.py": "enqueue_search_index_dispatch",
			"notification_delivery_service.py": "enqueue_notification_delivery_dispatch",
			"analytics_pipeline_service.py": "enqueue_analytics_ingest_dispatch",
		}
		root = Path(frappe.get_app_path("aos", "services"))
		offenders: list[str] = []
		for filename, function_name in service_files.items():
			tree = ast.parse((root / filename).read_text(encoding="utf-8"))
			fn = next(
				node
				for node in tree.body
				if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
			)
			for node in ast.walk(fn):
				if (
					isinstance(node, ast.Call)
					and isinstance(node.func, ast.Attribute)
					and node.func.attr == "commit"
				):
					offenders.append(f"{filename}:{node.lineno}")
		self.assertEqual(
			offenders, [], "Reusable outbox creation helpers must not commit the caller transaction"
		)

	def test_claiming_uses_skip_locked_and_owner_token_lease(self):
		source = inspect.getsource(__import__("aos.services.transactional_outbox", fromlist=["dummy"]))
		self.assertIn("FOR UPDATE SKIP LOCKED", source)
		self.assertIn("claimed_by", source)
		self.assertIn("claim_token", source)
		self.assertIn("lease_expires_at", source)
		self.assertIn("recover_stale_claims", source)

	def test_retry_backoff_is_bounded_exponential(self):
		self.assertEqual(retry_delay_seconds(1), 15)
		self.assertEqual(retry_delay_seconds(2), 30)
		self.assertEqual(retry_delay_seconds(3), 60)
		self.assertEqual(retry_delay_seconds(20), 3600)

	def test_duplicate_callback_is_tolerated_and_conflict_is_rejected(self):
		savepoint = f"outbox_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				job = self._analytics_job()
				outbox = ensure_outbox_for_job(
					service_type="analytics_ingestion", job=job, queue="short", timeout_seconds=300
				)
			outbox.status = "Published"
			outbox.save(ignore_permissions=True)

			first = mark_outbox_callback(
				job_doctype=job.doctype,
				job_name=job.name,
				callback_status="ingested",
				success=True,
			)
			duplicate = mark_outbox_callback(
				job_doctype=job.doctype,
				job_name=job.name,
				callback_status="ingested",
				success=True,
			)
			self.assertEqual(first.status, "Completed")
			self.assertEqual(duplicate.status, "Completed")
			with self.assertRaises(OutboxConflictError):
				mark_outbox_callback(
					job_doctype=job.doctype,
					job_name=job.name,
					callback_status="failed",
					success=False,
					error="late conflicting result",
				)
		finally:
			frappe.db.rollback(save_point=savepoint)

	def test_callback_idempotency_rejects_wrong_worker_correlation(self):
		job = SimpleNamespace(idempotency_key="stable-key")
		validate_callback_idempotency(job, {"idempotency_key": "stable-key"})
		validate_callback_idempotency(job, {})  # legacy signed worker compatibility
		with self.assertRaises(OutboxConflictError):
			validate_callback_idempotency(job, {"idempotency_key": "wrong-key"})

	def test_signed_worker_failure_is_terminal_completed_with_failure(self):
		savepoint = f"outbox_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				job = self._analytics_job()
				outbox = ensure_outbox_for_job(
					service_type="analytics_ingestion",
					job=job,
					queue="short",
					timeout_seconds=300,
					max_attempts=2,
				)
			outbox.status = "Published"
			outbox.attempt_count = 2
			outbox.max_attempts = 2
			outbox.save(ignore_permissions=True)
			result = mark_outbox_callback(
				job_doctype=job.doctype,
				job_name=job.name,
				callback_status="failed",
				success=False,
				error="sanitized downstream failure",
			)
			self.assertEqual(result.status, "Completed With Failure")
			self.assertIsNotNone(result.completed_at)
			self.assertIsNone(result.next_attempt_at)
		finally:
			frappe.db.rollback(save_point=savepoint)

	def test_publisher_commit_before_enqueue_gap_is_recoverable_from_persisted_row(self):
		source = inspect.getsource(__import__("aos.services.transactional_outbox", fromlist=["dummy"]))
		# The after-commit path is explicitly best effort; the minute scheduler invokes
		# the durable publisher for rows committed before an RQ enqueue/process crash.
		hooks = Path(frappe.get_app_path("aos", "hooks.py")).read_text(encoding="utf-8")
		self.assertIn("persisted row is recovered by the recurring publisher", source)
		self.assertIn("aos.tasks.outbox.publish_transactional_outbox", hooks)

	def test_duplicate_rq_dispatch_is_terminal_noop(self):
		from aos.services.transactional_outbox import dispatch_claimed_outbox

		terminal = SimpleNamespace(status="Completed")
		with patch("aos.services.transactional_outbox.frappe.get_doc", return_value=terminal):
			self.assertIs(dispatch_claimed_outbox("OUTBOX-1", "token"), terminal)

	def test_dead_letter_replay_requires_exact_key_and_is_bounded(self):
		outbox = MagicMock()
		outbox.name = "OUTBOX-2026-00001"
		outbox.status = "Dead Letter"
		outbox.idempotency_key = "stable-idempotency-key"
		outbox.attempt_count = 5
		outbox.max_attempts = 5
		outbox.service_type = "analytics_ingestion"

		with (
			patch("aos.services.transactional_outbox.frappe.only_for"),
			patch("aos.services.transactional_outbox.frappe.get_doc", return_value=outbox),
			patch("aos.services.transactional_outbox.frappe.db.sql"),
			patch("aos.services.transactional_outbox.frappe.db.commit") as commit,
			patch("aos.services.transactional_outbox.frappe.logger") as logger,
		):
			with self.assertRaises(OutboxConflictError):
				requeue_dead_letter_outbox(
					outbox_name=outbox.name,
					expected_idempotency_key="wrong-key",
				)

			result = requeue_dead_letter_outbox(
				outbox_name=outbox.name,
				expected_idempotency_key="stable-idempotency-key",
				additional_attempts=99,
			)

		self.assertEqual(result["status"], "Queued")
		self.assertEqual(result["additional_attempts"], 10)
		self.assertEqual(outbox.max_attempts, 15)
		self.assertIsNone(outbox.last_error)
		outbox.save.assert_called_once_with(ignore_permissions=True)
		commit.assert_called_once()
		logger.assert_called_once_with("aos.outbox", allow_site=True)

	def test_monitoring_includes_stale_failed_dead_letter_and_all_services(self):
		from aos.services.transactional_outbox import outbox_monitoring_summary

		status_rows = [
			SimpleNamespace(status="Queued", total=4),
			SimpleNamespace(status="Failed", total=2),
			SimpleNamespace(status="Claimed", total=1),
			SimpleNamespace(status="Dead Letter", total=3),
		]
		service_rows = [
			SimpleNamespace(service_type=service, status="Queued", total=1) for service in SERVICE_TYPES
		]
		db = MagicMock()
		lifecycle_rows = [
			SimpleNamespace(
				service_type=service,
				created_total=1,
				dispatched_total=1,
				completed_total=0,
				failed_total=0,
				retried_total=0,
				dead_lettered_total=0,
				callback_timeouts_total=0,
				redispatch_accepted_total=0,
				redispatch_skipped_total=0,
				redispatch_failure_total=0,
				duplicate_active_dispatch_total=0,
				callback_replay_total=0,
				old_generation_rejection_total=0,
				token_mismatch_total=0,
				transaction_rollback_total=0,
				duration_seconds_sum=0,
				duration_seconds_count=0,
			)
			for service in SERVICE_TYPES
		]
		db.sql.side_effect = [
			status_rows,
			[[120]],
			[[1]],
			[[0]],
			service_rows,
			lifecycle_rows,
			[[1]],
		]
		with patch("aos.services.transactional_outbox.frappe.db", db):
			report = outbox_monitoring_summary()
		self.assertEqual(report["queue_depth"], 6)
		self.assertEqual(report["stale_lease_count"], 1)
		self.assertEqual(report["dead_letter_count"], 3)
		self.assertEqual(set(report["by_service"]), set(SERVICE_TYPES))
