"""Authoritative status transition validation for Verification."""

from __future__ import annotations

from typing import Any

from .constants import (
    REVIEWER_TRANSITIONS,
    REVIEW_STATUSES,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUS_REVOKED,
    VERIFICATION_STATUSES,
)
from .errors import VerificationConflictError, VerificationPermissionError, VerificationValidationError


def is_reviewer(user: str | None) -> bool:
    """Lazy policy boundary keeps pure state-machine tests Frappe-independent."""
    from .policy import is_reviewer as policy_is_reviewer

    return policy_is_reviewer(user)


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
        if old_status not in {STATUS_REJECTED, STATUS_REVOKED} or status != STATUS_PENDING:
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
        doc.revoked_by = None
        doc.revoked_on = None
        doc.rejection_reason = None
        return
    if previous is None:
        doc.verified_by = None
        doc.verified_on = None
        doc.revoked_by = None
        doc.revoked_on = None
        return

    old_status = _clean(previous.status)
    new_status = _clean(doc.status)
    if old_status == new_status:
        if _clean(doc.verified_by) != _clean(previous.verified_by) or _clean(doc.verified_on) != _clean(
            previous.verified_on
        ):
            raise VerificationConflictError("Verification review metadata cannot be edited directly.")
        if _clean(doc.revoked_by) != _clean(previous.revoked_by) or _clean(doc.revoked_on) != _clean(
            previous.revoked_on
        ):
            raise VerificationConflictError("Verification revocation metadata cannot be edited directly.")
        if old_status == STATUS_REJECTED and _clean(doc.rejection_reason) != _clean(
            previous.rejection_reason
        ):
            raise VerificationConflictError("Rejected verification reason cannot be edited after decision.")
        return

    # Approval/rejection metadata belongs to its original decision. A later
    # revocation gets dedicated revocation metadata rather than overwriting it.
    if new_status not in {STATUS_APPROVED, STATUS_REJECTED}:
        doc.verified_by = previous.verified_by
        doc.verified_on = previous.verified_on
    if new_status != STATUS_REVOKED:
        doc.revoked_by = previous.revoked_by
        doc.revoked_on = previous.revoked_on


def stamp_review_metadata(doc, previous, *, actor: str | None) -> None:
    if previous is None or _clean(previous.status) == _clean(doc.status):
        return
    if doc.status not in {STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED}:
        return
    action = _clean(getattr(doc.flags, "aos_verification_action", ""))
    if not is_reviewer(actor) and action not in {"account_delete", "system"}:
        raise VerificationPermissionError("Only authorized reviewers can complete verification review.")

    from frappe.utils import now

    if doc.status == STATUS_REVOKED:
        doc.revoked_by = actor if actor and actor != "Guest" and action not in {"account_delete", "system"} else None
        doc.revoked_on = now()
    else:
        doc.verified_by = actor if actor and actor != "Guest" else None
        doc.verified_on = now()
        doc.revoked_by = None
        doc.revoked_on = None
    if doc.status != STATUS_REJECTED:
        doc.rejection_reason = None
