from __future__ import annotations

import os
import uuid
from contextlib import suppress
from typing import ClassVar
from unittest.mock import patch

import frappe
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
    requeue_dead_letter_outbox,
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
            result = recover_overdue_published(now=now_datetime())
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

    def test_callback_timeout_exhaustion_dead_letters(self):
        _job, outbox = self._job_and_outbox(max_attempts=2)
        outbox.status = "Published"
        outbox.attempt_count = 2
        outbox.max_attempts = 2
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-1, as_datetime=True)
        outbox.save(ignore_permissions=True)
        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={"state": "callback_pending", "work_state": "work_complete", "callback_state": "dead_letter"},
        ):
            result = recover_overdue_published(now=now_datetime())
        outbox.reload()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(outbox.status, "Dead Letter")
        self.assertIsNotNone(outbox.completed_at)
        self.assertIsNone(outbox.next_attempt_at)

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

    def test_scheduled_publisher_recovers_commit_before_immediate_enqueue(self):
        job, outbox = self._job_and_outbox()
        frappe.db.commit()
        self.committed_names.extend([(OUTBOX_DOCTYPE, outbox.name), (job.doctype, job.name)])
        calls: list[dict] = []

        def fake_enqueue(*_args, **kwargs):
            calls.append(kwargs)
            return object()

        with patch("aos.services.transactional_outbox.frappe.enqueue", side_effect=fake_enqueue):
            result = publish_outbox_records(limit=1, lease_seconds=60)
        outbox.reload()
        self.assertEqual(result["dispatched"], 1)
        self.assertEqual(outbox.status, "Queued")
        self.assertTrue(outbox.claim_token)
        self.assertEqual(len(calls), 1)
        self.assertIn(f":g{outbox.attempt_count}", calls[0]["job_id"])

    def test_callback_timeout_dead_letter_can_be_operator_replayed(self):
        _job, outbox = self._job_and_outbox(max_attempts=1)
        outbox.status = "Published"
        outbox.attempt_count = 1
        outbox.max_attempts = 1
        outbox.callback_deadline_at = add_to_date(now_datetime(), seconds=-1, as_datetime=True)
        outbox.save(ignore_permissions=True)
        with patch(
            "aos.services.transactional_outbox.query_companion_job_status",
            return_value={"state": "callback_pending", "work_state": "work_complete", "callback_state": "dead_letter"},
        ):
            recover_overdue_published(now=now_datetime())
        outbox.reload()
        self.assertEqual(outbox.status, "Dead Letter")
        with patch("aos.services.transactional_outbox.frappe.only_for"):
            result = requeue_dead_letter_outbox(
                outbox_name=outbox.name,
                expected_idempotency_key=outbox.idempotency_key,
                additional_attempts=2,
            )
        self.committed_names.append((OUTBOX_DOCTYPE, outbox.name))
        self.assertEqual(result["status"], "Queued")
        outbox.reload()
        self.assertEqual(outbox.max_attempts, 3)
        self.assertEqual(outbox.attempt_count, 1)
        self.assertIsNone(outbox.callback_deadline_at)

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
