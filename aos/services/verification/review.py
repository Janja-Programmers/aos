"""Authorized staff review actions for Verification Desk tooling."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import (
    MAX_REJECTION_REASON_LENGTH,
    REVIEW_ACTION_REJECT,
    REVIEW_ACTION_TRANSITIONS,
    VERIFICATION_DOCTYPE,
)
from .errors import (
    VerificationNotFoundError,
    VerificationValidationError,
)
from .policy import assert_reviewer


def review_verification_request(
    *,
    request_name: Any,
    action: Any,
    reason: Any = None,
    version: Any,
    reviewer: str,
):
    """Apply one explicit reviewer action to a Verification request.

    The DocType controller remains the state-machine authority. This boundary
    validates reviewer intent, captures the optimistic version loaded in Desk,
    and marks the save with the explicit action required by the lifecycle.
    """
    clean_name = _required_text(request_name, field="request_name", max_length=140)
    clean_action = _required_text(action, field="action", max_length=32).lower()
    clean_version = _required_text(version, field="version", max_length=64)

    assert_reviewer(reviewer)

    transition = REVIEW_ACTION_TRANSITIONS.get(clean_action)
    if not transition:
        raise VerificationValidationError("Invalid verification review action.")

    clean_reason = _clean_reason(reason)
    if clean_action == REVIEW_ACTION_REJECT and not clean_reason:
        raise VerificationValidationError("A rejection reason is required.")
    if clean_action != REVIEW_ACTION_REJECT and clean_reason:
        raise VerificationValidationError("A rejection reason is only valid when rejecting a request.")

    try:
        doc = frappe.get_doc(VERIFICATION_DOCTYPE, clean_name)
    except frappe.DoesNotExistError as exc:
        raise VerificationNotFoundError("Verification request not found.") from exc

    _allowed_sources, target_status = transition
    doc.flags.aos_verification_action = clean_action
    doc.flags.aos_verification_expected_modified = clean_version
    doc.status = target_status
    doc.rejection_reason = clean_reason if clean_action == REVIEW_ACTION_REJECT else None
    doc.save(ignore_permissions=True)
    return doc


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise VerificationValidationError(f"{field} is required.")
    clean = value.strip()
    if not clean or len(clean) > max_length:
        raise VerificationValidationError(f"Invalid {field}.")
    return clean


def _clean_reason(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > MAX_REJECTION_REASON_LENGTH:
        raise VerificationValidationError("Rejection reason is too long.")
    return text
