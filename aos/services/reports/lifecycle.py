"""Authoritative human-review lifecycle for report records."""

from __future__ import annotations

from typing import Any

from frappe.utils import now_datetime

from .constants import (
    REPORT_STATUSES,
    REPORT_TRANSITIONS,
    STATUS_REVIEWING,
    SUBMISSION_FIELDS,
)
from .errors import ReportConflictError, ReportValidationError
from .policy import require_reviewer
from .validation import clean_text


def _clean(value: Any) -> str:
    return str(value or "").strip()


def prepare_new_report(doc) -> None:
    doc.status = STATUS_REVIEWING
    if hasattr(doc, "reviewed_by"):
        doc.reviewed_by = None
    if hasattr(doc, "reviewed_on"):
        doc.reviewed_on = None


def validate_report_lifecycle(doc, previous, *, actor: str | None) -> None:
    status = _clean(getattr(doc, "status", ""))
    if status not in REPORT_STATUSES:
        raise ReportValidationError("Invalid report status.")

    _normalize_submission_details(doc)
    if previous is None:
        if status != STATUS_REVIEWING:
            raise ReportConflictError("New reports must start as Reviewing.")
        return

    _protect_submission(doc, previous)
    old_status = _clean(getattr(previous, "status", ""))
    status_changed = old_status != status
    if status_changed:
        require_reviewer(actor, doctype=doc.doctype)
        if status not in REPORT_TRANSITIONS.get(old_status, frozenset()):
            raise ReportConflictError("Report status transition is not allowed.")
    else:
        _protect_review_metadata(doc, previous)


def stamp_review_metadata(doc, previous, *, actor: str | None) -> None:
    if previous is None:
        if hasattr(doc, "reviewed_by"):
            doc.reviewed_by = None
        if hasattr(doc, "reviewed_on"):
            doc.reviewed_on = None
        return
    if _clean(previous.status) == _clean(doc.status):
        return
    require_reviewer(actor, doctype=doc.doctype)
    if hasattr(doc, "reviewed_by"):
        doc.reviewed_by = actor
    if hasattr(doc, "reviewed_on"):
        doc.reviewed_on = now_datetime()


def _normalize_submission_details(doc) -> None:
    from .constants import DETAIL_MAX_LENGTH

    if hasattr(doc, "details"):
        doc.details = clean_text(
            getattr(doc, "details", ""),
            field="details",
            max_length=DETAIL_MAX_LENGTH.get(doc.doctype, 1000),
            multiline=True,
            reject_html=True,
        )


def _protect_submission(doc, previous) -> None:
    for field in SUBMISSION_FIELDS.get(doc.doctype, ()):
        if _clean(getattr(doc, field, "")) != _clean(getattr(previous, field, "")):
            raise ReportConflictError("Submitted report details cannot be edited during review.")


def _protect_review_metadata(doc, previous) -> None:
    for field in ("reviewed_by", "reviewed_on"):
        if hasattr(doc, field) and _clean(getattr(doc, field, "")) != _clean(getattr(previous, field, "")):
            raise ReportConflictError("Report review metadata cannot be edited directly.")
