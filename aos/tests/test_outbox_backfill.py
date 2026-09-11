from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0.backfill_transactional_outbox import (
	BATCH_SIZE,
	SPECS,
	_backfill_job,
	_clear_stale_aggregate_link,
	_payload_malformed,
	_row_value,
	_should_backfill,
	execute,
)
from aos.services.transactional_outbox import OUTBOX_DOCTYPE, stable_idempotency_key


class TestOutboxBackfillUnit(FrappeTestCase):
	def test_all_five_legacy_job_types_are_covered(self):
		self.assertEqual(
			{spec.doctype for spec in SPECS},
			{
				"AOS Video Processing Job",
				"AOS Moderation Job",
				"AOS Search Index Job",
				"AOS Notification Delivery Job",
				"AOS Analytics Ingest Job",
			},
		)

	def test_retryable_and_terminal_status_policy(self):
		for spec in SPECS:
			self.assertTrue(_should_backfill("Queued", 0, 3, spec))
			self.assertTrue(_should_backfill("Failed", 1, 3, spec))
			self.assertFalse(_should_backfill("Failed", 3, 3, spec))
			for status in spec.completed_statuses | spec.permanent_statuses:
				self.assertFalse(_should_backfill(status, 0, 3, spec))

	def test_missing_and_malformed_payloads_are_safe(self):
		self.assertFalse(_payload_malformed(None))
		self.assertFalse(_payload_malformed(""))
		self.assertFalse(_payload_malformed('{"ok":true}'))
		self.assertTrue(_payload_malformed("{secret malformed"))

	def test_row_value_supports_mapping_and_object_rows(self):
		self.assertEqual(_row_value({"name": "DICT-1"}, "name"), "DICT-1")
		self.assertEqual(_row_value(SimpleNamespace(name="OBJECT-1"), "name"), "OBJECT-1")
		self.assertIsNone(_row_value(SimpleNamespace(), "name"))

	def test_deleted_historical_aggregate_is_cleared_before_save(self):
		outbox = SimpleNamespace(aggregate_doctype="AOS Short", aggregate_name="SHORT-MISSING")
		with patch(
			"aos.patches.v1_0.backfill_transactional_outbox.frappe.db.exists",
			side_effect=lambda doctype, name: doctype == "DocType",
		):
			_clear_stale_aggregate_link(outbox)
		self.assertIsNone(outbox.aggregate_doctype)
		self.assertIsNone(outbox.aggregate_name)

	def test_deterministic_idempotency_key_for_each_job_type(self):
		for spec in SPECS:
			first = stable_idempotency_key(spec.service_type, spec.doctype, "LEGACY-1")
			second = stable_idempotency_key(spec.service_type, spec.doctype, "LEGACY-1")
			self.assertEqual(first, second)

	def test_backfill_existing_outbox_is_not_duplicated_for_every_type(self):
		for spec in SPECS:
			job = MagicMock()
			job.doctype = spec.doctype
			job.name = f"LEGACY-{spec.service_type}"
			job.idempotency_key = ""
			job.request_payload = "{malformed"
			job.last_error = "sanitized legacy error"
			job.max_attempts = 3
			outbox = MagicMock()
			outbox.callback_timeout_seconds = 900
			counters = {
				"created": 0,
				"existing": 0,
				"skipped_terminal": 0,
				"malformed_payload": 0,
			}
			row = {"name": job.name, "status": "Queued", "attempt_count": 0, "max_attempts": 3}
			with (
				patch("aos.patches.v1_0.backfill_transactional_outbox.frappe.get_doc", return_value=job),
				patch(
					"aos.patches.v1_0.backfill_transactional_outbox.frappe.db.get_value",
					return_value="OUTBOX-1",
				),
				patch(
					"aos.patches.v1_0.backfill_transactional_outbox.ensure_outbox_for_job",
					return_value=outbox,
				) as ensure,
			):
				_backfill_job(spec, row, counters)
			self.assertEqual(
				job.idempotency_key, stable_idempotency_key(spec.service_type, spec.doctype, job.name)
			)
			self.assertEqual(counters["existing"], 1)
			self.assertEqual(counters["created"], 0)
			self.assertEqual(counters["malformed_payload"], 1)
			ensure.assert_not_called()
			outbox.save.assert_not_called()

	def test_execute_processes_large_tables_in_bounded_batches(self):
		spec = SPECS[-1]
		first_batch = [
			SimpleNamespace(name=f"ANLY-{index:04d}", status="Queued", attempt_count=0, max_attempts=3)
			for index in range(BATCH_SIZE)
		]
		second_batch = [SimpleNamespace(name="ANLY-9999", status="Queued", attempt_count=0, max_attempts=3)]
		sql_calls = 0

		def sql(_query, params, as_dict=False):
			nonlocal sql_calls
			sql_calls += 1
			cursor, limit = params
			self.assertLessEqual(int(limit), 1000)
			if cursor == "":
				return first_batch
			if cursor == first_batch[-1].name:
				return second_batch
			return []

		with (
			patch("aos.patches.v1_0.backfill_transactional_outbox.SPECS", (spec,)),
			patch("aos.patches.v1_0.backfill_transactional_outbox.frappe.db.table_exists", return_value=True),
			patch("aos.patches.v1_0.backfill_transactional_outbox.frappe.db.sql", side_effect=sql),
			patch("aos.patches.v1_0.backfill_transactional_outbox._backfill_job") as backfill,
			patch("aos.patches.v1_0.backfill_transactional_outbox.frappe.logger"),
		):
			report = execute(batch_size=BATCH_SIZE)
		self.assertEqual(report["scanned"], BATCH_SIZE + 1)
		self.assertEqual(backfill.call_count, BATCH_SIZE + 1)
		self.assertEqual(sql_calls, 3)


class TestOutboxBackfillDatabase(FrappeTestCase):
	def test_analytics_backfill_is_database_idempotent_and_rollback_safe(self):
		savepoint = f"backfill_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			job = frappe.get_doc(
				{
					"doctype": "AOS Analytics Ingest Job",
					"naming_series": "ANLY-JOB-.YYYY.-.#####",
					"status": "Queued",
					"events_json": "[]",
					"request_payload": "{malformed legacy payload",
					"attempt_count": 0,
					"max_attempts": 3,
				}
			).insert(ignore_permissions=True)
			spec = next(item for item in SPECS if item.doctype == job.doctype)
			counters = {"created": 0, "existing": 0, "skipped_terminal": 0, "malformed_payload": 0}
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				_backfill_job(
					spec,
					{"name": job.name, "status": "Queued", "attempt_count": 0, "max_attempts": 3},
					counters,
				)
				first = frappe.db.get_value(
					OUTBOX_DOCTYPE, {"job_doctype": job.doctype, "job_name": job.name}, "name"
				)
				existing_outbox = frappe.get_doc(OUTBOX_DOCTYPE, first)
				existing_outbox.last_error = "EXISTING_OUTBOX_MUST_NOT_BE_REWRITTEN"
				existing_outbox.save(ignore_permissions=True)
				_backfill_job(
					spec,
					{"name": job.name, "status": "Queued", "attempt_count": 0, "max_attempts": 3},
					counters,
				)
				second = frappe.db.get_value(
					OUTBOX_DOCTYPE, {"job_doctype": job.doctype, "job_name": job.name}, "name"
				)
			self.assertEqual(first, second)
			self.assertEqual(
				frappe.db.get_value(OUTBOX_DOCTYPE, first, "last_error"),
				"EXISTING_OUTBOX_MUST_NOT_BE_REWRITTEN",
			)
			self.assertEqual(counters["created"], 1)
			self.assertEqual(counters["existing"], 1)
			self.assertEqual(counters["malformed_payload"], 2)
		finally:
			frappe.db.rollback(save_point=savepoint)


class TestOutboxBackfillAllServiceTypesDatabase(FrappeTestCase):
	"""DocType-backed backfill coverage for every durable service-job family."""

	def _counters(self) -> dict[str, int]:
		return {
			"created": 0,
			"existing": 0,
			"skipped_terminal": 0,
			"malformed_payload": 0,
		}

	def _row(self, fixture) -> dict[str, object]:
		fixture.job.reload()
		return {
			"name": fixture.job.name,
			"status": fixture.job.status,
			"attempt_count": int(fixture.job.attempt_count or 0),
			"max_attempts": int(fixture.job.max_attempts or 3),
		}

	def test_each_doctype_backfills_retryable_states_and_skips_terminal_states(self):
		from aos.tests.outbox_fixtures import create_durable_job, create_outbox, legacy_spec

		completed = {
			"video_processing": "Ready",
			"moderation": "Allowed",
			"search_indexing": "Indexed",
			"notification_delivery": "Delivered",
			"analytics_ingestion": "Ingested",
		}
		savepoint = f"backfill_all_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			for service_type in completed:
				with self.subTest(service_type=service_type):
					spec = legacy_spec(service_type)

					queued = create_durable_job(service_type, status="Queued", malformed_payload=True)
					# Simulate a pre-hardening row without routing through current
					# DocType validation, which correctly rejects a missing key.
					frappe.db.set_value(
						queued.doctype,
						queued.job.name,
						"idempotency_key",
						None,
						update_modified=False,
					)
					queued_counters = self._counters()
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						_backfill_job(spec, self._row(queued), queued_counters)
					queued_outbox = frappe.get_doc(
						OUTBOX_DOCTYPE,
						frappe.db.get_value(
							OUTBOX_DOCTYPE,
							{"job_doctype": queued.doctype, "job_name": queued.job.name},
							"name",
						),
					)
					expected_key = stable_idempotency_key(service_type, queued.doctype, queued.job.name)
					queued.job.reload()
					self.assertEqual(queued.job.idempotency_key, expected_key)
					self.assertEqual(queued_outbox.status, "Queued")
					self.assertEqual(queued_counters["created"], 1)
					self.assertEqual(queued_counters["malformed_payload"], 1)

					missing = create_durable_job(service_type, status="Queued")
					frappe.db.set_value(
						missing.doctype,
						missing.job.name,
						"request_payload",
						None,
						update_modified=False,
					)
					missing.job.reload()
					missing_counters = self._counters()
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						_backfill_job(spec, self._row(missing), missing_counters)
					self.assertTrue(
						frappe.db.exists(
							OUTBOX_DOCTYPE,
							{"job_doctype": missing.doctype, "job_name": missing.job.name},
						)
					)
					self.assertEqual(missing_counters["created"], 1)
					self.assertEqual(missing_counters["malformed_payload"], 0)

					failed = create_durable_job(service_type, status="Failed")
					failed.job.attempt_count = 1
					failed.job.max_attempts = 4
					failed.job.last_error = "legacy internal detail must not be copied"
					failed.job.save(ignore_permissions=True)
					failed_counters = self._counters()
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						_backfill_job(spec, self._row(failed), failed_counters)
					failed_outbox = frappe.get_doc(
						OUTBOX_DOCTYPE,
						frappe.db.get_value(
							OUTBOX_DOCTYPE,
							{"job_doctype": failed.doctype, "job_name": failed.job.name},
							"name",
						),
					)
					self.assertEqual(failed_outbox.status, "Failed")
					self.assertEqual(failed_outbox.attempt_count, 1)
					self.assertEqual(failed_outbox.max_attempts, 4)
					self.assertEqual(failed_outbox.last_error, "LEGACY_RETRYABLE_FAILURE")

					processing = create_durable_job(service_type, status="Processing")
					legacy_service_job_id = f"legacy-rq-{service_type}"
					processing.job.idempotency_key = stable_idempotency_key(
						service_type, processing.doctype, processing.job.name
					)
					processing.job.service_job_id = legacy_service_job_id
					processing.job.attempt_count = 1
					processing.job.save(ignore_permissions=True)
					processing_counters = self._counters()
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						_backfill_job(spec, self._row(processing), processing_counters)
					processing_outbox = frappe.get_doc(
						OUTBOX_DOCTYPE,
						frappe.db.get_value(
							OUTBOX_DOCTYPE,
							{"job_doctype": processing.doctype, "job_name": processing.job.name},
							"name",
						),
					)
					processing.job.reload()
					self.assertEqual(processing.job.idempotency_key, legacy_service_job_id)
					self.assertEqual(processing_outbox.status, "Reconciliation Pending")
					self.assertEqual(processing_outbox.pending_dispatch_reason, "legacy_processing_reconciliation")
					self.assertIsNone(processing_outbox.callback_deadline_at)
					self.assertIsNotNone(processing_outbox.next_attempt_at)

					existing = create_durable_job(service_type, status="Queued")
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						original = create_outbox(existing)
					existing_counters = self._counters()
					with patch("aos.services.transactional_outbox.register_after_commit_publish"):
						_backfill_job(spec, self._row(existing), existing_counters)
						_backfill_job(spec, self._row(existing), existing_counters)
					names = frappe.get_all(
						OUTBOX_DOCTYPE,
						filters={"job_doctype": existing.doctype, "job_name": existing.job.name},
						pluck="name",
					)
					self.assertEqual(names, [original.name])
					self.assertEqual(existing_counters["existing"], 2)

					done = create_durable_job(service_type, status=completed[service_type])
					done_counters = self._counters()
					_backfill_job(spec, self._row(done), done_counters)
					self.assertFalse(
						frappe.db.exists(
							OUTBOX_DOCTYPE,
							{"job_doctype": done.doctype, "job_name": done.job.name},
						)
					)
					self.assertEqual(done_counters["skipped_terminal"], 1)

					exhausted = create_durable_job(service_type, status="Failed")
					exhausted.job.attempt_count = 3
					exhausted.job.max_attempts = 3
					exhausted.job.save(ignore_permissions=True)
					exhausted_counters = self._counters()
					_backfill_job(spec, self._row(exhausted), exhausted_counters)
					self.assertFalse(
						frappe.db.exists(
							OUTBOX_DOCTYPE,
							{"job_doctype": exhausted.doctype, "job_name": exhausted.job.name},
						)
					)
					self.assertEqual(exhausted_counters["skipped_terminal"], 1)
		finally:
			frappe.db.rollback(save_point=savepoint)

	def test_mixed_all_service_patch_execution_is_idempotent_batched_and_rollback_safe(self):
		from aos.tests.outbox_fixtures import create_durable_job

		savepoint = f"backfill_mixed_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		fixtures = [create_durable_job(spec.service_type, status="Queued") for spec in SPECS]
		names = [(fixture.doctype, fixture.job.name) for fixture in fixtures]
		try:
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				first = execute(batch_size=10)
				second = execute(batch_size=10)
			self.assertGreaterEqual(first["created"], len(fixtures))
			self.assertGreaterEqual(second["existing"], len(fixtures))
			for doctype, name in names:
				outboxes = frappe.get_all(
					OUTBOX_DOCTYPE,
					filters={"job_doctype": doctype, "job_name": name},
					pluck="name",
				)
				self.assertEqual(len(outboxes), 1)
		finally:
			frappe.db.rollback(save_point=savepoint)
		for doctype, name in names:
			self.assertFalse(frappe.db.exists(doctype, name))
			self.assertFalse(frappe.db.exists(OUTBOX_DOCTYPE, {"job_doctype": doctype, "job_name": name}))
