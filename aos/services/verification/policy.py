"""Central Verification ownership, account-state, and reviewer policy."""

from __future__ import annotations

import frappe

from aos.services.sellers.policy import get_or_create_seller, seller_capabilities

from .constants import REVIEWER_ROLE, TYPE_BUSINESS
from .errors import VerificationNotFoundError, VerificationPermissionError


def lock_eligible_profile(user: str):
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        raise VerificationPermissionError("Authentication is required.", code="AUTH_REQUIRED", http_status=401)
    rows = frappe.db.sql(
        """
        SELECT p.name
        FROM `tabAOS Profile` p
        INNER JOIN `tabUser` u ON u.name = p.user
        WHERE p.user = %s
        LIMIT 1
        FOR UPDATE
        """,
        (clean_user,),
        as_dict=True,
    )
    if not rows:
        raise VerificationNotFoundError("Profile not found.", code="PROFILE_NOT_FOUND")
    profile = frappe.get_doc("AOS Profile", rows[0].name)
    enabled = frappe.db.get_value("User", clean_user, "enabled")
    status = str(getattr(profile, "account_status", "Active") or "Active")
    deleted = bool(int(getattr(profile, "is_deleted", 0) or 0))
    if deleted or status == "Deleted":
        raise VerificationPermissionError("Account is unavailable.", code="ACCOUNT_DELETED")
    if status == "Suspended":
        raise VerificationPermissionError("Account is suspended.", code="ACCOUNT_SUSPENDED")
    if status == "Deactivated":
        raise VerificationPermissionError("Account is deactivated.", code="ACCOUNT_DEACTIVATED")
    if int(enabled or 0) != 1 or status != "Active":
        raise VerificationPermissionError("Account is unavailable.", code="ACCOUNT_DISABLED")
    return profile


def ensure_business_seller(user: str):
    seller = get_or_create_seller(user)
    if not seller:
        raise VerificationPermissionError("Seller profile is required.", code="SELLER_REQUIRED")
    if not seller_capabilities(seller).get("can_submit_verification"):
        raise VerificationPermissionError("Seller profile is unavailable.", code="SELLER_INACTIVE")
    return seller


def assert_submission_eligible(*, user: str, verification_type: str):
    profile = lock_eligible_profile(user)
    if bool(int(getattr(profile, "is_verified", 0) or 0)):
        raise VerificationPermissionError(
            "Account is already verified.", code="VERIFICATION_ALREADY_APPROVED", http_status=409
        )
    seller = ensure_business_seller(user) if verification_type == TYPE_BUSINESS else None
    return profile, seller


def is_reviewer(user: str | None) -> bool:
    clean_user = str(user or "").strip()
    if not clean_user or clean_user == "Guest":
        return False
    return REVIEWER_ROLE in set(frappe.get_roles(clean_user) or [])


def assert_reviewer(user: str | None) -> None:
    if not is_reviewer(user):
        raise VerificationPermissionError("Verification review access denied.")
