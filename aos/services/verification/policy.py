"""Central Verification ownership, account-state, and reviewer policy."""

from __future__ import annotations

import frappe

from aos.services.accounts.constants import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DELETED,
    ACCOUNT_STATUS_SUSPENDED,
)
from aos.services.accounts.repository import AccountRepository
from aos.utils.doctype_permissions import has_doctype_permission

from .constants import VERIFICATION_DOCTYPE
from .errors import VerificationNotFoundError, VerificationPermissionError


def lock_profile(user: str):
    """Lock and return the canonical Accounts profile without changing its lifecycle."""
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        raise VerificationPermissionError("Authentication is required.", code="AUTH_REQUIRED", http_status=401)

    profile = AccountRepository.lock_profile(clean_user)
    if not profile:
        raise VerificationNotFoundError("Profile not found.", code="PROFILE_NOT_FOUND")
    return profile


def lock_eligible_profile(user: str):
    """Lock the canonical Accounts profile and verify it can submit/be approved."""
    clean_user = str(user or "").strip()
    profile = lock_profile(clean_user)
    status = str(getattr(profile, "account_status", ACCOUNT_STATUS_ACTIVE) or ACCOUNT_STATUS_ACTIVE)
    if status == ACCOUNT_STATUS_DELETED:
        raise VerificationPermissionError("Account is unavailable.", code="ACCOUNT_DELETED")
    if status == ACCOUNT_STATUS_SUSPENDED:
        raise VerificationPermissionError("Account is suspended.", code="ACCOUNT_SUSPENDED")
    enabled = int(frappe.db.get_value("User", clean_user, "enabled") or 0)
    if enabled != 1 or status != ACCOUNT_STATUS_ACTIVE:
        raise VerificationPermissionError("Account is unavailable.", code="ACCOUNT_DISABLED")
    return profile


def assert_submission_eligible(*, user: str):
    """Serialize submission against the authoritative Accounts lifecycle row."""
    return lock_eligible_profile(user)


def is_reviewer(user: str | None) -> bool:
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        return False
    return has_doctype_permission(
        user=clean_user,
        doctype=VERIFICATION_DOCTYPE,
        ptype="write",
    )


def assert_reviewer(user: str | None) -> None:
    if not is_reviewer(user):
        raise VerificationPermissionError("Verification review access denied.")
