from __future__ import annotations

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.patches.v1_0.finalize_outbox_failure_reconciliation import execute
from aos.services import analytics_pipeline_service
from aos.services.transactional_outbox import (
	OUTBOX_DOCTYPE,
	OutboxConflictError,
	authorize_terminal_work_replay,
)
from aos.tests.outbox_fixtures import cleanup_committed_records, create_durable_job, create_outbox


class _AcceptedResponse:
	content = b"{}"

	def __init__(self, payload: dict[str, object] | None = None) -> None:
		self._payload = payload or {
			"ok": True,
			"service_job_id": "stable-analytics-job",
			"dispatch_action": "enqueued",
		}

	def raise_for_status(self) -> None:
		return None

	def json(self) -> dict[str, object]:
		return dict(self._payload)


class TestFinalizeOutboxFailureReconciliationPatch(FrappeTestCase):
	def test_patch_terminalizes_failure_callback_and_is_idempotent(self):
		savepoint = f"final_failure_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			fixture = create_durable_job("analytics_ingestion", status="Failed")
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				outbox = create_outbox(fixture)
			outbox.status = "Failed"
			outbox.callback_status = "failed"
			outbox.callback_received_at = now_datetime()
			outbox.next_attempt_at = now_datetime()
			outbox.save(ignore_permissions=True)

			first = execute(batch_size=50, names=[outbox.name])
			outbox.reload()
			self.assertEqual(outbox.status, "Completed With Failure")
			self.assertIsNone(outbox.next_attempt_at)
			self.assertGreaterEqual(first["normalized"], 1)
			completed_at = outbox.completed_at
			execute(batch_size=50, names=[outbox.name])
			outbox.reload()
			self.assertEqual(outbox.status, "Completed With Failure")
			self.assertEqual(outbox.completed_at, completed_at)
		finally:
			frappe.db.rollback(save_point=savepoint)

	def test_patch_normalizes_legacy_claim_and_exhausted_reconciliation(self):
		savepoint = f"final_reconcile_{uuid.uuid4().hex[:12]}"
		frappe.db.savepoint(savepoint)
		try:
			claimed_fixture = create_durable_job("video_processing", status="Queued")
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				claimed = create_outbox(claimed_fixture)
			claimed.status = "Claimed"
			claimed.dispatch_generation = 4
			claimed.companion_authoritative_generation = 1
			claimed.claim_token = uuid.uuid4().hex
			claimed.claimed_by = "legacy-publisher"
			claimed.save(ignore_permissions=True)

			recon_fixture = create_durable_job("moderation", status="Processing")
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				recon = create_outbox(recon_fixture)
			recon.status = "Reconciliation Pending"
			recon.reconciliation_attempt_count = 3
			recon.reconciliation_max_attempts = 3
			recon.save(ignore_permissions=True)

			uncertain_fixture = create_durable_job("notification_delivery", status="Processing")
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				uncertain = create_outbox(uncertain_fixture)
			uncertain.status = "Dispatch Uncertain"
			uncertain.attempt_count = 4
			uncertain.max_attempts = 4
			uncertain.save(ignore_permissions=True)

			active_fixture = create_durable_job("analytics_ingestion", status="Processing")
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				active = create_outbox(active_fixture)
			active.status = "Dispatch Uncertain"
			active.attempt_count = 5
			active.max_attempts = 5
			active.claimed_by = "active-publisher"
			active.claim_token = uuid.uuid4().hex
			active.lease_expires_at = add_to_date(now_datetime(), minutes=5, as_datetime=True)
			active.save(ignore_permissions=True)

			execute(
				batch_size=50,
				names=[claimed.name, recon.name, uncertain.name, active.name],
			)
			claimed.reload()
			recon.reload()
			uncertain.reload()
			active.reload()
			self.assertEqual(claimed.status, "Queued")
			self.assertEqual(claimed.companion_authoritative_generation, 4)
			self.assertEqual(recon.status, "Manual Review")
			self.assertEqual(recon.manual_review_reason, "LEGACY_RECONCILIATION_ATTEMPTS_EXHAUSTED")
			self.assertEqual(uncertain.status, "Manual Review")
			self.assertEqual(uncertain.manual_review_reason, "LEGACY_DISPATCH_UNCERTAINTY_EXHAUSTED")
			self.assertEqual(active.status, "Dispatch Uncertain")
			self.assertEqual(active.claimed_by, "active-publisher")
			self.assertIsNotNone(active.claim_token)
		finally:
			frappe.db.rollback(save_point=savepoint)


class TestAnalyticsDispatchExceptionSafety(FrappeTestCase):
	def test_outbox_conflict_before_action_assignment_preserves_original_error(self):
		fixture = None
		try:
			fixture = create_durable_job("analytics_ingestion", status="Queued")
			# The dispatch path commits its durable state before contacting the
			# companion, so a savepoint cannot be used for test isolation here.
			frappe.db.commit()
			with patch.object(analytics_pipeline_service.requests, "post", return_value=_AcceptedResponse()), patch.object(
				analytics_pipeline_service,
				"record_companion_dispatch_outcome",
				side_effect=OutboxConflictError("newer generation", error_code="NEWER_DISPATCH_GENERATION"),
			), patch.dict(
				"os.environ",
				{
					"ANALYTICS_PIPELINE_ENABLED": "true",
					"ANALYTICS_SERVICE_URL": "http://127.0.0.1:18170",
					"ANALYTICS_SERVICE_SECRET": "test-analytics-secret",
					"ANALYTICS_CALLBACK_URL": "http://127.0.0.1:8000/callback",
				},
				clear=False,
			):
				with self.assertRaises(OutboxConflictError) as raised:
					analytics_pipeline_service.dispatch_analytics_ingest_job(fixture.job.name)
			self.assertEqual(raised.exception.error_code, "NEWER_DISPATCH_GENERATION")
			fixture.job.reload()
			self.assertNotEqual(fixture.job.last_error, "UnboundLocalError")
		finally:
			if fixture is not None:
				for doctype, name in fixture.cleanup_records:
					frappe.db.delete(doctype, {"name": name})
			frappe.db.commit()



class TestTerminalWorkReplay(FrappeTestCase):
	def test_system_manager_work_replay_reopens_same_job_and_outbox(self):
		fixture = None
		outbox = None
		previous_user = frappe.session.user or "Guest"
		frappe.set_user("Administrator")
		try:
			fixture = create_durable_job("analytics_ingestion", status="Failed")
			with patch("aos.services.transactional_outbox.register_after_commit_publish"):
				outbox = create_outbox(fixture)
			outbox.status = "Completed With Failure"
			outbox.callback_status = "failed"
			outbox.completed_at = now_datetime()
			outbox.attempt_count = 3
			outbox.max_attempts = 3
			outbox.save(ignore_permissions=True)
			frappe.db.commit()
			job_name = fixture.job.name

			with patch(
				"aos.services.transactional_outbox._companion_status_config",
				return_value=("http://127.0.0.1:18170", "test-secret", "X-Test-Signature"),
			), patch(
				"aos.services.transactional_outbox.requests.post",
				return_value=_AcceptedResponse(
					{"ok": True, "outcome": "work_replay_authorized", "replay_count": 1}
				),
			):
				result = authorize_terminal_work_replay(
					outbox_name=outbox.name,
					expected_idempotency_key=outbox.idempotency_key,
					additional_attempts=2,
				)
			outbox.reload()
			fixture.job.reload()
			self.assertEqual(result["status"], "Queued")
			self.assertEqual(outbox.status, "Queued")
			self.assertEqual(outbox.work_replay_count, 1)
			self.assertGreaterEqual(outbox.max_attempts, 5)
			self.assertEqual(fixture.job.name, job_name)
			self.assertEqual(fixture.job.status, "Queued")
		finally:
			try:
				frappe.set_user("Administrator")
				records: list[tuple[str, str]] = []
				if outbox is not None:
					records.append((OUTBOX_DOCTYPE, outbox.name))
				if fixture is not None:
					records.extend(fixture.cleanup_records)
				cleanup_committed_records(records)
			finally:
				frappe.set_user(previous_user)
