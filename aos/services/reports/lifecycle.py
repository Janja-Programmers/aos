"""Authoritative human-review lifecycle for all existing Report DocTypes."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from .constants import (
    REPORT_ACTIONS,
    REPORT_STATUSES,
    REPORT_TRANSITIONS,
    STATUS_REJECTED,
    STATUS_RESOLVED,
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
    if hasattr(doc, "admin_action"):
        doc.admin_action = ""
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
        _validate_action(doc)
        return

    _protect_submission(doc, previous)
    old_status = _clean(getattr(previous, "status", ""))
    status_changed = old_status != status
    action_changed = _clean(getattr(previous, "admin_action", "")) != _clean(getattr(doc, "admin_action", ""))

    if status_changed or action_changed:
        require_reviewer(actor, doctype=doc.doctype)

    if status_changed and status not in REPORT_TRANSITIONS.get(old_status, frozenset()):
        raise ReportConflictError("Report status transition is not allowed.")

    _validate_action(doc)
    previous_action = _clean(getattr(previous, "admin_action", ""))
    current_action = _clean(getattr(doc, "admin_action", ""))
    if previous_action and previous_action != current_action:
        raise ReportConflictError("A completed moderation action cannot be changed.")

    if not status_changed and not action_changed:
        _protect_review_metadata(doc, previous)


def stamp_review_metadata(doc, previous, *, actor: str | None) -> None:
    if previous is None:
        if hasattr(doc, "reviewed_by"):
            doc.reviewed_by = None
        if hasattr(doc, "reviewed_on"):
            doc.reviewed_on = None
        return
    old_status = _clean(previous.status)
    new_status = _clean(doc.status)
    old_action = _clean(getattr(previous, "admin_action", ""))
    new_action = _clean(getattr(doc, "admin_action", ""))
    if old_status == new_status and old_action == new_action:
        return
    require_reviewer(actor, doctype=doc.doctype)
    if hasattr(doc, "reviewed_by"):
        doc.reviewed_by = actor
    if hasattr(doc, "reviewed_on"):
        doc.reviewed_on = now_datetime()


def _normalize_submission_details(doc) -> None:
    # DocType validation is a final integrity boundary even when a report is
    # created outside a public endpoint.
    from .constants import DETAIL_MAX_LENGTH

    if hasattr(doc, "details"):
        doc.details = clean_text(
            getattr(doc, "details", ""),
            field="details",
            max_length=DETAIL_MAX_LENGTH.get(doc.doctype, 1000),
            multiline=True,
        )


def _protect_submission(doc, previous) -> None:
    for field in SUBMISSION_FIELDS.get(doc.doctype, ()):
        if _clean(getattr(doc, field, "")) != _clean(getattr(previous, field, "")):
            raise ReportConflictError("Submitted report details cannot be edited during review.")


def _protect_review_metadata(doc, previous) -> None:
    for field in ("reviewed_by", "reviewed_on"):
        if hasattr(doc, field) and _clean(getattr(doc, field, "")) != _clean(getattr(previous, field, "")):
            raise ReportConflictError("Report review metadata cannot be edited directly.")


def _validate_action(doc) -> None:
    if not hasattr(doc, "admin_action"):
        return
    action = _clean(getattr(doc, "admin_action", ""))
    allowed = REPORT_ACTIONS.get(doc.doctype, frozenset({""}))
    if action not in allowed:
        raise ReportValidationError("Invalid moderation action.")
    status = _clean(getattr(doc, "status", ""))
    if action and status != STATUS_RESOLVED:
        raise ReportValidationError("Admin action requires a resolved report.")
    if status == STATUS_REJECTED and action:
        raise ReportValidationError("Rejected reports cannot apply a moderation action.")
