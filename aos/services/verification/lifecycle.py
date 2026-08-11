"""Authoritative status transition validation for the Verification DocType."""

from __future__ import annotations

from typing import Any

from frappe.utils import now

from .constants import (
    REVIEWER_TRANSITIONS,
    REVIEW_STATUSES,
    STATUS_PENDING,
    STATUS_REJECTED,
    VERIFICATION_STATUSES,
)
from .errors import VerificationConflictError, VerificationPermissionError, VerificationValidationError
from .policy import is_reviewer


def _clean(value: Any) -> str:
    return str(value or "").strip()


def validate_status_transition(doc, previous, *, actor: str | None) -> None:
    status = _clean(doc.status)
    if status not in VERIFICATION_STATUSES:
        raise VerificationValidationError("Invalid verification status.")
    if previous is None:
        if status != STATUS_PENDING:
            raise VerificationConflictError("New verification requests must start as Pending.")
        return

    old_status = _clean(previous.status)
    if old_status == status:
        return

    action = _clean(getattr(doc.flags, "aos_verification_action", ""))
    if action == "resubmit":
        if old_status not in {"Rejected", "Revoked"} or status != STATUS_PENDING:
            raise VerificationConflictError("Verification request cannot be resubmitted in its current state.")
        return
    if action in {"account_delete", "system"}:
        return

    if status in REVIEW_STATUSES or old_status in REVIEW_STATUSES:
        if not is_reviewer(actor):
            raise VerificationPermissionError("Only authorized reviewers can change verification status.")
        if status not in REVIEWER_TRANSITIONS.get(old_status, frozenset()):
            raise VerificationConflictError("Verification status transition is not allowed.")
        return

    raise VerificationConflictError("Verification status transition is not allowed.")


def protect_review_metadata(doc, previous) -> None:
    """Prevent client/Desk mutation of server-owned decision metadata."""
    action = _clean(getattr(doc.flags, "aos_verification_action", ""))
    if action == "resubmit":
        doc.verified_by = None
        doc.verified_on = None
        doc.rejection_reason = None
        return
    if previous is None:
        doc.verified_by = None
        doc.verified_on = None
        return

    old_status = _clean(previous.status)
    new_status = _clean(doc.status)
    if old_status == new_status:
        if _clean(doc.verified_by) != _clean(previous.verified_by) or _clean(doc.verified_on) != _clean(
            previous.verified_on
        ):
            raise VerificationConflictError("Verification review metadata cannot be edited directly.")
        if old_status == STATUS_REJECTED and _clean(doc.rejection_reason) != _clean(
            previous.rejection_reason
        ):
            raise VerificationConflictError("Rejected verification reason cannot be edited after decision.")
        return

    if new_status not in {"Approved", "Rejected", "Revoked"}:
        doc.verified_by = previous.verified_by
        doc.verified_on = previous.verified_on


def stamp_review_metadata(doc, previous, *, actor: str | None) -> None:
    if previous is None or _clean(previous.status) == _clean(doc.status):
        return
    if doc.status not in {"Approved", "Rejected", "Revoked"}:
        return
    if not is_reviewer(actor) and _clean(getattr(doc.flags, "aos_verification_action", "")) not in {
        "account_delete",
        "system",
    }:
        raise VerificationPermissionError("Only authorized reviewers can complete verification review.")
    if doc.status == "Revoked" and _clean(previous.status) == "Approved":
        # The existing schema has no dedicated revocation actor/timestamp. Keep
        # the original approval decision immutable rather than overwriting it.
        doc.verified_by = previous.verified_by
        doc.verified_on = previous.verified_on
    else:
        doc.verified_by = actor if actor and actor != "Guest" else None
        doc.verified_on = now()
    if doc.status != STATUS_REJECTED:
        doc.rejection_reason = None
