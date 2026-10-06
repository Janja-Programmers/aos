"""Staff-only manual Report review boundary.

Reports never auto-resolve. This module is the only supported server path for
changing a Report from Reviewing to a terminal decision.
"""

from __future__ import annotations

from typing import Any

import frappe

from .constants import (
    REPORT_DOCTYPE_BY_TYPE,
    REVIEW_NOTE_MAX_LENGTH,
    STATUS_REJECTED,
    STATUS_RESOLVED,
    STATUS_REVIEWING,
)
from .errors import ReportConflictError, ReportNotFoundError, ReportValidationError
from .observability import report_log
from .policy import require_reviewer
from .validation import clean_text


_TABLES = {doctype: f"tab{doctype}" for doctype in REPORT_DOCTYPE_BY_TYPE.values()}


def _normalize_type(value: Any) -> tuple[str, str]:
    report_type = clean_text(value, field="report_type", max_length=20, required=True).lower()
    doctype = REPORT_DOCTYPE_BY_TYPE.get(report_type)
    if not doctype:
        raise ReportValidationError("Unsupported report type.")
    return report_type, doctype


def _lock_report(*, doctype: str, report_id: str):
    table = _TABLES[doctype]
    rows = frappe.db.sql(
        f"SELECT name FROM `{table}` WHERE name = %s LIMIT 1 FOR UPDATE",
        (report_id,),
        as_dict=True,
    )
    if not rows:
        raise ReportNotFoundError("Report not found.")
    return frappe.get_doc(doctype, rows[0].name)


def review_report(
    *,
    report_type: Any,
    report_id: Any,
    decision: Any,
    note: Any,
    version: Any,
    reviewer: str,
):
    clean_type, doctype = _normalize_type(report_type)
    reviewer = str(reviewer or "").strip()
    require_reviewer(reviewer, doctype=doctype)

    clean_id = clean_text(report_id, field="report_id", max_length=140, required=True)
    clean_decision = clean_text(decision, field="decision", max_length=20, required=True).lower()
    if clean_decision not in {"resolve", "reject"}:
        raise ReportValidationError("Invalid report decision.")
    clean_note = clean_text(
        note,
        field="decision_note",
        max_length=REVIEW_NOTE_MAX_LENGTH,
        multiline=True,
        reject_html=True,
    )
    if clean_decision == "reject" and not clean_note:
        raise ReportValidationError("A rejection note is required.")
    if clean_decision == "resolve" and clean_note:
        raise ReportValidationError("A decision note is only valid when rejecting a report.")

    doc = _lock_report(doctype=doctype, report_id=clean_id)
    expected_version = clean_text(version, field="version", max_length=32, required=True)
    if expected_version != str(doc.modified or ""):
        raise ReportConflictError("Report changed since it was opened. Reload it and try again.")
    if str(doc.status or "") != STATUS_REVIEWING:
        raise ReportConflictError("Report is no longer awaiting review.")

    doc.flags.aos_report_manual_review = True
    doc.status = STATUS_RESOLVED if clean_decision == "resolve" else STATUS_REJECTED
    doc.decision_note = clean_note or None
    doc.save(ignore_permissions=True)

    report_log(
        "report.manually_reviewed",
        report_id=str(doc.name),
        doctype=doctype,
        status=str(doc.status),
    )
    return {
        "report_id": str(doc.name),
        "report_type": clean_type,
        "status": str(doc.status),
        "reviewed_by": str(doc.reviewed_by or ""),
        "reviewed_on": str(doc.reviewed_on or ""),
        "version": str(doc.modified or ""),
    }
