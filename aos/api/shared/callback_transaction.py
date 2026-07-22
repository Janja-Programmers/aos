"""Atomic transaction boundary for signed companion-service callbacks."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import frappe

from aos.services.transactional_outbox import (
	OUTBOX_DOCTYPE,
	OutboxConflictError,
	_ACTIVE_CALLBACK_CORRELATION,
)

_COUNTER_FIELDS = {
	"old_generation_rejection_count",
	"token_mismatch_count",
	"transaction_rollback_count",
}
_CALLBACK_MANAGER_NAMES = (
	"before_commit",
	"after_commit",
	"before_rollback",
	"after_rollback",
)
_CALLBACK_JOB_DOCTYPES = {
	"AOS Video Processing Job",
	"AOS Moderation Job",
	"AOS Search Index Job",
	"AOS Notification Delivery Job",
	"AOS Analytics Ingest Job",
}


def _increment_counter(outbox_name: str | None, fieldname: str) -> None:
	if not outbox_name or fieldname not in _COUNTER_FIELDS:
		return
	frappe.db.sql(
		f"""
        UPDATE `tab{OUTBOX_DOCTYPE}`
        SET `{fieldname}` = COALESCE(`{fieldname}`, 0) + 1
        WHERE name = %s
        """,
		(outbox_name,),
	)


def _snapshot_transaction_callbacks() -> dict[str, list[Any]]:
	"""Capture Frappe callback-manager lists so savepoint rollback is complete.

	Frappe's SQL savepoint cannot remove an in-memory after-commit callback that
	was registered by a domain helper. On callback failure we restore the callback
	manager lists as well as the database state, preventing rolled-back work from
	running after the wrapper returns a sanitized response.
	"""

	snapshot: dict[str, list[Any]] = {}
	for name in _CALLBACK_MANAGER_NAMES:
		manager = getattr(frappe.db, name, None)
		functions = getattr(manager, "_functions", None)
		if isinstance(functions, list):
			snapshot[name] = list(functions)
	return snapshot


def _restore_transaction_callbacks(snapshot: dict[str, list[Any]]) -> None:
	for name, functions in snapshot.items():
		manager = getattr(frappe.db, name, None)
		current = getattr(manager, "_functions", None)
		if isinstance(current, list):
			current[:] = functions


def _outbox_registration_flag() -> bool | None:
	flags = getattr(getattr(frappe, "local", None), "flags", None)
	if flags is None:
		return None
	return bool(getattr(flags, "aos_outbox_after_commit_registered", False))


def _restore_outbox_registration_flag(value: bool | None) -> None:
	flags = getattr(getattr(frappe, "local", None), "flags", None)
	if flags is None or value is None:
		return
	flags.aos_outbox_after_commit_registered = value


def _lock_callback_records(job_doctype: str | None, job_name: str | None) -> str | None:
	"""Serialize callbacks for one durable job before any mutation.

	The doctype is a repository constant supplied by each callback endpoint, not
	request input. Locking the outbox first makes an exact duplicate wait until the
	first callback commits; it then observes terminal state and becomes a no-op.
	"""

	if not job_doctype or not job_name:
		return None
	if job_doctype not in _CALLBACK_JOB_DOCTYPES:
		raise ValueError("Unsupported callback job type.")
	outbox_rows = frappe.db.sql(
		f"""
        SELECT name
        FROM `tab{OUTBOX_DOCTYPE}`
        WHERE job_doctype = %s AND job_name = %s
        LIMIT 1
        FOR UPDATE
        """,
		(job_doctype, job_name),
	)
	frappe.db.sql(
		f"SELECT name FROM `tab{job_doctype}` WHERE name = %s LIMIT 1 FOR UPDATE",
		(job_name,),
	)
	return str(outbox_rows[0][0]) if outbox_rows else None


def execute_callback_atomically[T](
	*,
	service_type: str,
	operation: Callable[[], T],
	job_doctype: str | None = None,
	job_name: str | None = None,
) -> T:
	"""Run one callback with domain, durable-job, and outbox writes atomically.

	The API wrapper may sanitize an exception and return normally only after this
	helper restores both SQL state and any in-memory transaction callbacks created
	after the savepoint. Bounded audit counters are written after rollback and do
	not preserve partial business mutations.
	"""

	savepoint = f"aos_callback_{uuid.uuid4().hex[:16]}"
	callbacks_before = _snapshot_transaction_callbacks()
	registration_flag_before = _outbox_registration_flag()
	frappe.db.savepoint(savepoint)
	correlated_outbox: str | None = None
	correlation_token = _ACTIVE_CALLBACK_CORRELATION.set(None)
	try:
		correlated_outbox = _lock_callback_records(job_doctype, job_name)
		return operation()
	except Exception as exc:
		frappe.db.rollback(save_point=savepoint)
		_restore_transaction_callbacks(callbacks_before)
		_restore_outbox_registration_flag(registration_flag_before)
		outbox_name = getattr(exc, "outbox_name", None) or correlated_outbox
		_increment_counter(outbox_name, "transaction_rollback_count")
		if isinstance(exc, OutboxConflictError):
			_increment_counter(outbox_name, getattr(exc, "counter_field", ""))
		try:
			from aos.utils.metrics import record_outbox_event

			record_outbox_event(service_type, "transaction_rollback")
		except Exception:
			pass
		raise
	finally:
		_ACTIVE_CALLBACK_CORRELATION.reset(correlation_token)
