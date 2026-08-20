from __future__ import annotations

import os
import uuid
from contextlib import suppress
from typing import ClassVar
from unittest.mock import patch

import frappe
from frappe.exceptions import TimestampMismatchError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from aos.services.transactional_outbox import (
    CALLBACK_TIMEOUT_ERROR,
    OUTBOX_DOCTYPE,
    OutboxConflictError,
    _claim_one,
    _mark_dispatch_uncertain,
    _repair_callback_completed_outbox,
    _schedule_reconciliation,
    ensure_outbox_for_job,
    mark_outbox_callback,
    publish_outbox_records,
    recover_overdue_published,
    recover_stale_claims,
    validate_callback_idempotency,
)


class TestTransactionalOutboxRecovery(FrappeTestCase):
    """Database-backed lost-callback, crash recovery, and lease behavior."""

    committed_names: ClassVar[list[tuple[str, str]]] = []

    def tearDown(self) -> None:
        for doctype, name in reversed(self.committed_names):
            with suppress(Exception):
                frappe.db.delete(doctype, {"name": name})
        if self.committed_names:
            frappe.db.commit()
        self.committed_names.clear()
        super().tearDown()

    def _job_and_outbox(self, *, max_attempts: int = 3):
        job = frappe.get_doc(
            {
                "doctype": "AOS Analytics Ingest Job",
                "naming_series": "ANLY-JOB-.YYYY.-.#####",
                "status": "Queued",
                "events_json": "[]",
                "attempt_count": 0,
                "max_attempts": max_attempts,
                "idempotency_key": uuid.uuid4().hex,
            }
        ).insert(ignore_permissions=True)
        with patch("aos.services.transactional_outbox.register_after_commit_publish"):
            outbox = ensure_outbox_for_job(
                service_type="analytics_ingestion",
                job=job,
                queue="short",
                timeout_seconds=300,
                max_attempts=max_attempts,
            )
        return job, outbox

    def test_successful_outer_commit_persists_domain_job_and_outbox(self):
        job, outbox = self._job_and_outbox()
        frappe.db.commit()
        self.committed_names.extend([(OUTBOX_DOCTYPE, outbox.name), (job.doctype, job.name)])
        self.assertTrue(frappe.db.exists(job.doctype, job.name))
        self.assertTrue(frappe.db.exists(OUTBOX_DOCTYPE, outbox.name))

    def test_published_without_callback_requeues_with_same_stable_id(self):
        _job, outbox = self._job_and_outbox(max_attempts=3)
        stable = outbox.idempotency_key
        old_token = uuid.uuid4().hex
        outbox.status = "Published"
        outbox.attempt_count = 1
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = old_token
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-60, as_datetime=True)
        outbox.save(ignore_permissions=True)

        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={"state": "callback_pending", "work_state": "work_complete", "callback_state": "pending"},
        ):
            result = recover_overdue_published(now=now_datetime(), outbox_name=outbox.name)
        outbox.reload()
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(outbox.status, "Reconciliation Pending")
        self.assertEqual(outbox.callback_timeout_count, 1)
        self.assertEqual(outbox.last_error, CALLBACK_TIMEOUT_ERROR)
        self.assertEqual(outbox.idempotency_key, stable)

        claim = _claim_one(
            owner="test-publisher",
            lease_seconds=60,
            now=outbox.next_attempt_at,
            outbox_name=outbox.name,
        )
        self.assertIsNotNone(claim)
        outbox.reload()
        self.assertEqual(outbox.attempt_count, 2)
        self.assertEqual(outbox.dispatch_generation, 1)
        self.assertEqual(outbox.proposed_dispatch_generation, 2)
        self.assertEqual(outbox.idempotency_key, stable)
        self.assertEqual(outbox.current_dispatch_token, old_token)
        self.assertNotEqual(outbox.proposed_dispatch_token, old_token)

    def test_concurrent_recovery_update_is_skipped_without_failing_publisher(self):
        _job, outbox = self._job_and_outbox(max_attempts=3)
        outbox.status = "Published"
        outbox.attempt_count = 1
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = uuid.uuid4().hex
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-60, as_datetime=True)
        outbox.save(ignore_permissions=True)

        outbox_class = outbox.__class__
        original_save = outbox_class.save

        def concurrent_save(doc, *args, **kwargs):
            if doc.name == outbox.name:
                raise TimestampMismatchError("concurrent outbox update")
            return original_save(doc, *args, **kwargs)

        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={"state": "queued", "work_state": "retrying", "callback_state": "not_ready"},
        ), patch.object(outbox_class, "save", new=concurrent_save):
            result = recover_overdue_published(now=now_datetime(), outbox_name=outbox.name)

        self.assertEqual(result["concurrent_updates_skipped"], 1)
        outbox.reload()
        self.assertEqual(outbox.status, "Published")

    def test_dispatch_uncertain_reuses_proposed_generation_and_token(self):
        _job, outbox = self._job_and_outbox(max_attempts=4)
        proposed_token = uuid.uuid4().hex
        outbox.status = "Dispatch Uncertain"
        outbox.attempt_count = 1
        outbox.dispatch_generation = 0
        outbox.proposed_dispatch_generation = 1
        outbox.proposed_dispatch_token = proposed_token
        outbox.pending_dispatch_reason = "dispatch_uncertain"
        outbox.next_attempt_at = now_datetime()
        outbox.save(ignore_permissions=True)
        claim = _claim_one(
            owner="uncertain-reconciliation",
            lease_seconds=60,
            now=now_datetime(),
            outbox_name=outbox.name,
        )
        self.assertIsNotNone(claim)
        self.assertEqual(claim["dispatch_generation"], 1)
        self.assertEqual(claim["dispatch_token"], proposed_token)
        outbox.reload()
        self.assertEqual(outbox.proposed_dispatch_generation, 1)
        self.assertEqual(outbox.proposed_dispatch_token, proposed_token)

    def test_callback_timeout_exhaustion_enters_callback_resolvable_manual_review(self):
        _job, outbox = self._job_and_outbox(max_attempts=2)
        outbox.status = "Published"
        outbox.attempt_count = 2
        outbox.max_attempts = 2
        outbox.reconciliation_max_attempts = 1
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-1, as_datetime=True)
        outbox.save(ignore_permissions=True)
        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={
                "state": "callback_pending",
                "work_state": "work_complete",
                "callback_state": "dead_letter",
                "dispatch_generation": int(outbox.dispatch_generation or 0),
            },
        ):
            result = recover_overdue_published(
                now=now_datetime(), outbox_name=outbox.name
            )
        outbox.reload()
        self.assertEqual(result["manual_review"], 1)
        self.assertEqual(result["dead_lettered"], 0)
        self.assertEqual(outbox.status, "Manual Review")
        self.assertIsNotNone(outbox.completed_at)
        self.assertIsNone(outbox.next_attempt_at)

    def test_targeted_callback_recovery_does_not_touch_unrelated_rows(self):
        _job1, outbox1 = self._job_and_outbox()
        _job2, outbox2 = self._job_and_outbox()
        for outbox in (outbox1, outbox2):
            outbox.status = "Published"
            outbox.attempt_count = 1
            outbox.dispatch_generation = 1
            outbox.current_dispatch_token = uuid.uuid4().hex
            outbox.callback_deadline_at = add_to_date(
                now_datetime(), seconds=-1, as_datetime=True
            )
            outbox.save(ignore_permissions=True)

        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={
                "state": "callback_pending",
                "work_state": "work_complete",
                "callback_state": "pending",
                "dispatch_generation": 1,
            },
        ):
            result = recover_overdue_published(
                now=now_datetime(), outbox_name=outbox1.name
            )

        outbox1.reload()
        outbox2.reload()
        self.assertEqual(result["requeued"], 1)
        self.assertEqual(outbox1.status, "Reconciliation Pending")
        self.assertEqual(outbox2.status, "Published")

    def test_old_callback_after_retry_is_rejected_and_success_is_idempotent(self):
        job, outbox = self._job_and_outbox()
        old_token = uuid.uuid4().hex
        new_token = uuid.uuid4().hex
        outbox.status = "Published"
        outbox.attempt_count = 2
        outbox.dispatch_generation = 2
        outbox.current_dispatch_token = new_token
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=300, as_datetime=True)
        outbox.save(ignore_permissions=True)

        with self.assertRaises(OutboxConflictError):
            validate_callback_idempotency(
                job,
                {
                    "idempotency_key": job.idempotency_key,
                    "dispatch_id": outbox.idempotency_key,
                    "dispatch_generation": 1,
                    "dispatch_token": old_token,
                    "status": "ingested",
                },
                callback_status="ingested",
            )
        validate_callback_idempotency(
            job,
            {
                "idempotency_key": job.idempotency_key,
                "dispatch_id": outbox.idempotency_key,
                "dispatch_generation": 2,
                "dispatch_token": new_token,
                "status": "ingested",
            },
            callback_status="ingested",
        )
        first = mark_outbox_callback(
            job_doctype=job.doctype,
            job_name=job.name,
            callback_status="ingested",
            success=True,
            dispatch_token=new_token,
            dispatch_generation=2,
        )
        duplicate = mark_outbox_callback(
            job_doctype=job.doctype,
            job_name=job.name,
            callback_status="ingested",
            success=True,
            dispatch_token=new_token,
            dispatch_generation=2,
        )
        self.assertEqual(first.status, "Completed")
        self.assertEqual(duplicate.status, "Completed")
        with self.assertRaises(OutboxConflictError):
            mark_outbox_callback(
                job_doctype=job.doctype,
                job_name=job.name,
                callback_status="failed",
                success=False,
                dispatch_token=old_token,
            )

    def test_callback_completion_retries_once_after_timestamp_mismatch(self):
        job, outbox = self._job_and_outbox()
        token = uuid.uuid4().hex
        outbox.status = "Published"
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = token
        outbox.save(ignore_permissions=True)

        validate_callback_idempotency(
            job,
            {
                "idempotency_key": job.idempotency_key,
                "dispatch_id": outbox.idempotency_key,
                "dispatch_generation": 1,
                "dispatch_token": token,
                "status": "ingested",
            },
            callback_status="ingested",
        )

        outbox_class = outbox.__class__
        original_save = outbox_class.save
        callback_save_attempts = 0

        def mismatch_once(doc, *args, **kwargs):
            nonlocal callback_save_attempts
            if doc.name == outbox.name and doc.status == "Completed":
                callback_save_attempts += 1
                if callback_save_attempts == 1:
                    raise TimestampMismatchError("simulated concurrent outbox update")
            return original_save(doc, *args, **kwargs)

        with patch.object(outbox_class, "save", new=mismatch_once):
            marked = mark_outbox_callback(
                job_doctype=job.doctype,
                job_name=job.name,
                callback_status="ingested",
                success=True,
                dispatch_token=token,
                dispatch_generation=1,
            )

        self.assertEqual(callback_save_attempts, 2)
        self.assertEqual(marked.status, "Completed")
        outbox.reload()
        self.assertEqual(outbox.status, "Completed")
        self.assertEqual(outbox.callback_status, "ingested")
        self.assertIsNone(outbox.current_dispatch_token)

    def test_scheduled_publisher_recovers_commit_before_immediate_enqueue(self):
        job, outbox = self._job_and_outbox()
        frappe.db.commit()
        self.committed_names.extend([(OUTBOX_DOCTYPE, outbox.name), (job.doctype, job.name)])
        calls: list[dict] = []

        def fake_enqueue(*_args, **kwargs):
            calls.append(kwargs)
            return object()

        with patch("aos.services.transactional_outbox.frappe.enqueue", side_effect=fake_enqueue):
            result = publish_outbox_records(limit=1, lease_seconds=60, outbox_name=outbox.name)
        outbox.reload()
        self.assertEqual(result["dispatched"], 1)
        self.assertEqual(outbox.status, "Queued")
        self.assertTrue(outbox.claim_token)
        self.assertEqual(len(calls), 1)
        self.assertIn(f":g{outbox.attempt_count}", calls[0]["job_id"])

    def test_callback_timeout_manual_review_accepts_a_valid_late_callback(self):
        job, outbox = self._job_and_outbox(max_attempts=1)
        token = uuid.uuid4().hex
        outbox.status = "Published"
        outbox.attempt_count = 1
        outbox.max_attempts = 1
        outbox.reconciliation_max_attempts = 1
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = token
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-1, as_datetime=True)
        outbox.save(ignore_permissions=True)
        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={
                "state": "callback_pending",
                "work_state": "work_complete",
                "callback_state": "dead_letter",
                "dispatch_generation": 1,
            },
        ):
            recover_overdue_published(now=now_datetime(), outbox_name=outbox.name)
        outbox.reload()
        self.assertEqual(outbox.status, "Manual Review")

        validate_callback_idempotency(
            job,
            {
                "idempotency_key": job.idempotency_key,
                "dispatch_id": outbox.idempotency_key,
                "dispatch_generation": 1,
                "dispatch_token": token,
                "status": "ingested",
            },
            callback_status="ingested",
        )
        completed = mark_outbox_callback(
            job_doctype=job.doctype,
            job_name=job.name,
            callback_status="ingested",
            success=True,
            dispatch_token=token,
            dispatch_generation=1,
        )
        self.assertEqual(completed.status, "Completed")

    def test_two_database_connections_cannot_lock_same_due_row(self):
        """Exercise MariaDB SKIP LOCKED with separate physical connections."""
        try:
            import pymysql
        except ImportError:
            self.skipTest("PyMySQL is unavailable in this Frappe test environment")
        job, outbox = self._job_and_outbox()
        frappe.db.commit()
        self.committed_names.extend([(OUTBOX_DOCTYPE, outbox.name), (job.doctype, job.name)])
        config = {
            "host": getattr(frappe.conf, "db_host", None) or "127.0.0.1",
            "port": int(getattr(frappe.conf, "db_port", 3306) or 3306),
            "user": getattr(frappe.conf, "db_user", None) or getattr(frappe.conf, "db_name", ""),
            "password": getattr(frappe.conf, "db_password", ""),
            "database": getattr(frappe.conf, "db_name", ""),
            "autocommit": False,
        }
        try:
            first = pymysql.connect(**config)
            second = pymysql.connect(**config)
        except Exception as exc:
            self.skipTest(f"Separate MariaDB connections are unavailable: {exc.__class__.__name__}")
        try:
            with first.cursor() as cursor:
                cursor.execute(
                    f"SELECT name FROM `tab{OUTBOX_DOCTYPE}` WHERE name=%s FOR UPDATE SKIP LOCKED",
                    (outbox.name,),
                )
                self.assertEqual(cursor.fetchone()[0], outbox.name)
            with second.cursor() as cursor:
                cursor.execute(
                    f"SELECT name FROM `tab{OUTBOX_DOCTYPE}` WHERE name=%s FOR UPDATE SKIP LOCKED",
                    (outbox.name,),
                )
                self.assertIsNone(cursor.fetchone())
        except Exception as exc:
            if "SKIP LOCKED" in str(exc).upper() or "syntax" in str(exc).lower():
                self.skipTest("CI MariaDB does not support SELECT FOR UPDATE SKIP LOCKED")
            raise
        finally:
            first.rollback()
            second.rollback()
            first.close()
            second.close()

    def test_stale_claim_recovery_skips_row_locked_by_active_worker(self):
        """Recovery must never wait on a worker that still owns the row lock."""
        try:
            import pymysql
        except ImportError:
            self.skipTest("PyMySQL is unavailable in this Frappe test environment")

        job, outbox = self._job_and_outbox()
        outbox.status = "Queued"
        outbox.claimed_by = "stale-worker"
        outbox.claim_token = uuid.uuid4().hex
        outbox.claimed_at = add_to_date(now_datetime(), seconds=-120, as_datetime=True)
        outbox.lease_expires_at = add_to_date(now_datetime(), seconds=-60, as_datetime=True)
        outbox.save(ignore_permissions=True)
        frappe.db.commit()
        self.committed_names.extend([(OUTBOX_DOCTYPE, outbox.name), (job.doctype, job.name)])

        config = {
            "host": getattr(frappe.conf, "db_host", None) or "127.0.0.1",
            "port": int(getattr(frappe.conf, "db_port", 3306) or 3306),
            "user": getattr(frappe.conf, "db_user", None) or getattr(frappe.conf, "db_name", ""),
            "password": getattr(frappe.conf, "db_password", ""),
            "database": getattr(frappe.conf, "db_name", ""),
            "autocommit": False,
        }
        try:
            blocker = pymysql.connect(**config)
        except Exception as exc:
            self.skipTest(f"Separate MariaDB connection is unavailable: {exc.__class__.__name__}")

        try:
            with blocker.cursor() as cursor:
                cursor.execute(
                    f"SELECT name FROM `tab{OUTBOX_DOCTYPE}` WHERE name=%s FOR UPDATE",
                    (outbox.name,),
                )
                self.assertEqual(cursor.fetchone()[0], outbox.name)

            try:
                skipped = recover_stale_claims(
                    now=now_datetime(), outbox_name=outbox.name, limit=1
                )
            except Exception as exc:
                if "SKIP LOCKED" in str(exc).upper() or "syntax" in str(exc).lower():
                    self.skipTest("CI MariaDB does not support SELECT FOR UPDATE SKIP LOCKED")
                raise
            self.assertEqual(skipped, 0)
        finally:
            blocker.rollback()
            blocker.close()

        recovered = recover_stale_claims(
            now=now_datetime(), outbox_name=outbox.name, limit=1
        )
        self.assertEqual(recovered, 1)
        outbox.reload()
        self.assertIsNone(outbox.claim_token)
        self.assertIsNone(outbox.lease_expires_at)
        self.assertEqual(outbox.pending_dispatch_reason, "publisher_lease_expired")

    def test_terminal_failure_callback_is_not_redispatched(self):
        job, outbox = self._job_and_outbox(max_attempts=5)
        token = uuid.uuid4().hex
        job.status = "Processing"
        job.save(ignore_permissions=True)
        outbox.status = "Published"
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = token
        outbox.save(ignore_permissions=True)
        validate_callback_idempotency(
            job,
            {
                "idempotency_key": job.idempotency_key,
                "dispatch_id": outbox.idempotency_key,
                "dispatch_generation": 1,
                "dispatch_token": token,
                "status": "failed",
            },
            callback_status="failed",
        )
        marked = mark_outbox_callback(
            job_doctype=job.doctype,
            job_name=job.name,
            callback_status="failed",
            success=False,
            error="ANALYTICS_INGESTION_FAILED",
            dispatch_token=token,
            dispatch_generation=1,
        )
        self.assertEqual(marked.status, "Completed With Failure")
        self.assertIsNone(marked.next_attempt_at)
        self.assertIsNone(
            _claim_one(owner="terminal-failure", lease_seconds=60, now=now_datetime(), outbox_name=outbox.name)
        )

    def test_valid_callback_remains_eligible_while_publisher_holds_lease(self):
        job, outbox = self._job_and_outbox(max_attempts=5)
        token = uuid.uuid4().hex
        outbox.status = "Reconciliation Pending"
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = token
        outbox.next_attempt_at = now_datetime()
        outbox.save(ignore_permissions=True)
        claim = _claim_one(owner="lease-race", lease_seconds=60, now=now_datetime(), outbox_name=outbox.name)
        self.assertIsNotNone(claim)
        outbox.reload()
        self.assertEqual(outbox.status, "Reconciliation Pending")
        self.assertTrue(outbox.claim_token)
        validate_callback_idempotency(
            job,
            {
                "idempotency_key": job.idempotency_key,
                "dispatch_id": outbox.idempotency_key,
                "dispatch_generation": 1,
                "dispatch_token": token,
                "status": "ingested",
            },
            callback_status="ingested",
        )
        marked = mark_outbox_callback(
            job_doctype=job.doctype,
            job_name=job.name,
            callback_status="ingested",
            success=True,
            dispatch_token=token,
            dispatch_generation=1,
        )
        self.assertEqual(marked.status, "Completed")
        self.assertIsNone(marked.claim_token)

    def test_companion_generation_floor_advances_next_proposal(self):
        _job, outbox = self._job_and_outbox(max_attempts=5)
        outbox.status = "Reconciliation Pending"
        outbox.dispatch_generation = 1
        outbox.companion_authoritative_generation = 5
        outbox.next_attempt_at = now_datetime()
        outbox.save(ignore_permissions=True)
        claim = _claim_one(owner="generation-floor", lease_seconds=60, now=now_datetime(), outbox_name=outbox.name)
        self.assertEqual(int(claim["dispatch_generation"]), 6)

    def test_reconciliation_exhaustion_moves_to_manual_review(self):
        _job, outbox = self._job_and_outbox(max_attempts=5)
        outbox.reconciliation_max_attempts = 2
        outbox.reconciliation_attempt_count = 1
        status = _schedule_reconciliation(
            outbox,
            now=now_datetime(),
            outcome="unknown",
            error="COMPANION_STATUS_UNRESOLVED",
        )
        self.assertEqual(status, "Manual Review")
        self.assertIsNone(outbox.next_attempt_at)


    def test_dispatch_uncertainty_exhaustion_moves_to_callback_resolvable_manual_review(self):
        job, outbox = self._job_and_outbox(max_attempts=1)
        token = uuid.uuid4().hex
        outbox.status = "Dispatch Uncertain"
        outbox.attempt_count = 1
        outbox.dispatch_generation = 1
        outbox.current_dispatch_token = token
        outbox.claim_token = "claim-token"
        outbox.save(ignore_permissions=True)
        status = _mark_dispatch_uncertain(outbox.name, "claim-token", "DOWNSTREAM_TRANSPORT_OUTCOME_UNKNOWN")
        self.assertEqual(status, "Manual Review")
        outbox.reload()
        self.assertEqual(outbox.status, "Manual Review")
        self.assertIsNone(outbox.next_attempt_at)
        validate_callback_idempotency(
            job,
            {
                "idempotency_key": job.idempotency_key,
                "dispatch_id": outbox.idempotency_key,
                "dispatch_generation": 1,
                "dispatch_token": token,
                "status": "ingested",
            },
            callback_status="ingested",
        )
        marked = mark_outbox_callback(
            job_doctype=job.doctype,
            job_name=job.name,
            callback_status="ingested",
            success=True,
            dispatch_token=token,
            dispatch_generation=1,
        )
        self.assertEqual(marked.status, "Completed")
        self.assertIsNone(marked.manual_review_reason)

    def test_callback_already_completed_repairs_terminal_failure_outbox(self):
        job, outbox = self._job_and_outbox(max_attempts=5)
        job.status = "Failed"
        job.last_error = "ANALYTICS_INGESTION_FAILED"
        job.save(ignore_permissions=True)
        outbox.status = "Reconciliation Pending"
        status = _repair_callback_completed_outbox(
            outbox,
            {
                "state": "failed",
                "work_state": "work_failed",
                "callback_state": "complete",
                "dispatch_generation": 3,
                "terminal_result_type": "failed",
                "result_digest": "a" * 64,
            },
            now=now_datetime(),
        )
        self.assertEqual(status, "Completed With Failure")
        self.assertEqual(outbox.completed_dispatch_generation, 3)
        self.assertEqual(outbox.terminal_result_digest, "a" * 64)
