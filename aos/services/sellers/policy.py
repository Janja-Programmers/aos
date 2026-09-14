"""Canonical Seller ownership, lifecycle and capability policy."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.db import is_duplicate_entry_error
from aos.services.notifications.service import NotificationService

from .constants import (
    SELLER_DOCTYPE,
    SELLER_STATUSES,
    SELLER_TYPE_BUSINESS,
    SELLER_TYPE_INDIVIDUAL,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_REASON_CODE_MAX_LENGTH,
    STATUS_SOURCE_MAX_LENGTH,
    STATUS_SUSPENDED,
)
from .errors import SellerNotFoundError, SellerPermissionError, SellerStateError, SellerValidationError
from .observability import seller_log
from .repository import get_by_user, lock_by_name, lock_verification_for_user
from .validation import normalize_business_category

_ALLOWED_TRANSITIONS = {
    STATUS_ACTIVE: frozenset({STATUS_SUSPENDED, STATUS_CLOSED}),
    STATUS_SUSPENDED: frozenset({STATUS_ACTIVE, STATUS_CLOSED}),
    STATUS_CLOSED: frozenset(),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def get_seller_for_user(user: str, *, require_active: bool = False, fields: list[str] | None = None):
    clean_user = _clean(user)
    if not clean_user or clean_user == "Guest":
        return None
    selected = list(
        dict.fromkeys(
            [
                "name",
                "public_id",
                "user",
                "status",
                "seller_type",
                "business_category",
                "storefront_version",
                "has_location",
                *(fields or []),
            ]
        )
    )
    row = get_by_user(clean_user, fields=selected)
    if not row:
        return None
    if require_active and _clean(row.status) != STATUS_ACTIVE:
        raise SellerStateError(
            "Seller account is not active.",
            code="SELLER_INACTIVE",
            http_status=403,
            data={"status": _clean(row.status) or None},
        )
    return row


def require_active_seller(user: str):
    row = get_seller_for_user(user, require_active=True)
    if not row:
        raise SellerNotFoundError("Seller profile is required.", code="SELLER_REQUIRED", http_status=403)
    return row


def get_or_create_seller(user: str):
    """Return the canonical Seller or create it idempotently for this account.

    The unique ``user`` constraint makes concurrent retries converge on one
    Seller. Creation also locks the user's canonical Verification row so a
    concurrent approval/revocation cannot be projected before the Seller exists
    while creation still uses a stale verification snapshot. Public-ID collisions
    are retried with a fresh random identifier; the database uniqueness
    constraint remains authoritative.
    """

    clean_user = _clean(user)
    if not clean_user or clean_user == "Guest":
        raise SellerPermissionError("Login is required.", code="AUTH_REQUIRED", http_status=401)
    existing = frappe.db.get_value(SELLER_DOCTYPE, {"user": clean_user}, "name")
    if existing:
        return frappe.get_doc(SELLER_DOCTYPE, existing)
    if not frappe.db.exists("User", clean_user) or not frappe.db.exists("AOS Profile", {"user": clean_user}):
        raise SellerValidationError("Account does not exist.", code="ACCOUNT_NOT_FOUND", http_status=404)

    verification = lock_verification_for_user(clean_user)
    # Re-check after acquiring the Verification lock: a concurrent creator may
    # have completed while this transaction was waiting for the same account.
    existing = frappe.db.get_value(SELLER_DOCTYPE, {"user": clean_user}, "name")
    if existing:
        return frappe.get_doc(SELLER_DOCTYPE, existing)

    approved_business = bool(
        verification
        and verification.status == "Approved"
        and verification.verification_type == SELLER_TYPE_BUSINESS
    )
    business_category = (
        normalize_business_category(verification.business_category)
        if approved_business and verification.business_category
        else None
    )

    for attempt in range(8):
        doc = frappe.get_doc(
            {
                "doctype": SELLER_DOCTYPE,
                "user": clean_user,
                "seller_type": SELLER_TYPE_BUSINESS if approved_business else SELLER_TYPE_INDIVIDUAL,
                "business_category": business_category,
                "status": STATUS_ACTIVE,
                "status_reason_code": "SELLER_CREATED",
                "status_source": "seller_service",
                "status_changed_at": now_datetime(),
                "storefront_version": 0,
                "location_version": 0,
            }
        )
        doc.flags.aos_seller_lifecycle_action = "create"
        doc.flags.aos_seller_trusted_projection = "verification" if approved_business else None
        savepoint = f"seller_create_{attempt}"
        frappe.db.savepoint(savepoint)
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            frappe.db.rollback(save_point=savepoint)
            existing = frappe.db.get_value(SELLER_DOCTYPE, {"user": clean_user}, "name")
            if existing:
                return frappe.get_doc(SELLER_DOCTYPE, existing)
            # The only remaining expected unique race is the random public ID.
            # Rebuild a fresh document so before_insert allocates a new ID.
            continue
        seller_log("seller.status.changed", seller_id=doc.name, status=STATUS_ACTIVE, operation="create")
        return doc

    # A 100-bit public identifier colliding eight consecutive times indicates
    # an infrastructure/randomness failure rather than normal contention.
    seller_log("seller.create.failed", operation="create", outcome="public_id_collision_exhausted")
    raise SellerStateError(
        "Seller could not be created safely. Try again.",
        code="SELLER_CREATE_CONFLICT",
        http_status=409,
    )


def set_seller_status(
    seller_id: Any,
    *,
    status: str,
    reason_code: str,
    source: str,
    actor: str | None = None,
):
    """Apply one explicit, locked Seller operational lifecycle transition."""

    clean_id = _clean(seller_id)
    new_status = _clean(status)
    if not clean_id:
        raise SellerNotFoundError("Seller not found.")
    if new_status not in SELLER_STATUSES:
        raise SellerValidationError("Invalid seller status.", code="INVALID_SELLER_STATUS")
    reason = _clean(reason_code)[:STATUS_REASON_CODE_MAX_LENGTH]
    source_value = _clean(source)[:STATUS_SOURCE_MAX_LENGTH]
    if not reason or not source_value:
        raise SellerValidationError("Seller status reason and source are required.", code="INVALID_SELLER_STATUS")

    doc = lock_by_name(clean_id)
    if not doc:
        raise SellerNotFoundError("Seller not found.")
    old_status = _clean(doc.status)
    if old_status == new_status:
        return doc, False
    if new_status not in _ALLOWED_TRANSITIONS.get(old_status, frozenset()):
        raise SellerStateError(
            "Seller status transition is not allowed.",
            code="SELLER_STATUS_TRANSITION_NOT_ALLOWED",
            http_status=409,
            data={"status": old_status, "requested_status": new_status},
        )

    doc.status = new_status
    doc.status_reason_code = reason
    doc.status_source = source_value
    doc.status_changed_at = now_datetime()
    doc.flags.aos_seller_lifecycle_action = source_value
    doc.flags.aos_seller_status_actor = _clean(actor)
    doc.save(ignore_permissions=True)

    try:
        NotificationService.notify_seller_status_changed(
            user=doc.user,
            seller_id=doc.public_id,
            old_status=old_status,
            new_status=new_status,
            reason_code=reason,
            transition_token=str(doc.status_changed_at or ""),
        )
    except Exception:
        # NotificationService is already savepoint-isolated. This boundary is
        # defensive so notification failure never corrupts Seller state.
        frappe.log_error(frappe.get_traceback(), "AOS Seller status notification failed")

    seller_log(
        "seller.status.changed",
        seller_id=doc.name,
        status=new_status,
        operation=source_value,
        outcome="success",
    )
    return doc, True


def sync_verification_projection(
    *,
    user: str,
    verification_type: str,
    verification_status: str,
    business_category: Any = None,
    source: str = "verification",
):
    """Project canonical Verification facts without changing Seller status.

    Verification owns the decision/status. Seller only projects the verified
    seller type for storefront behavior. Rejection/revocation never suspends or
    activates the Seller operational lifecycle.
    """

    row = get_seller_for_user(user)
    if not row:
        return None

    # Verification decisions and Seller storefront/lifecycle/location writes may
    # occur concurrently. Lock and reload the Seller before applying the
    # projection so we never overwrite a newer Seller write with a stale doc.
    doc = lock_by_name(row.name)
    if not doc or _clean(doc.status) == STATUS_CLOSED:
        return None

    approved_business = verification_status == "Approved" and verification_type == SELLER_TYPE_BUSINESS
    desired_type = SELLER_TYPE_BUSINESS if approved_business else SELLER_TYPE_INDIVIDUAL
    changed = str(doc.seller_type or "") != desired_type
    if changed:
        doc.seller_type = desired_type
    if approved_business and not str(doc.business_category or "").strip() and business_category:
        doc.business_category = normalize_business_category(business_category) or None
        changed = True
    if changed:
        doc.flags.aos_seller_trusted_projection = _clean(source) or "verification"
        doc.save(ignore_permissions=True)
    return doc


def require_storefront_update_allowed(user: str):
    row = get_seller_for_user(user)
    if not row:
        raise SellerNotFoundError("Seller profile not found.")
    status = _clean(row.status)
    if status != STATUS_ACTIVE:
        raise SellerPermissionError(
            "Seller storefront cannot be updated while the seller is inactive.",
            code="SELLER_UPDATE_NOT_ALLOWED",
            http_status=403,
            data={"status": status or None},
        )
    return row


def seller_capabilities(row: Any | None) -> dict[str, bool]:
    status = _clean(getattr(row, "status", None) if row is not None else None)
    active = status == STATUS_ACTIVE
    return {
        "is_active": active,
        "can_post_ads": active,
        "can_update_storefront": active,
        "can_manage_location": active,
        "can_submit_verification": status in {STATUS_ACTIVE, STATUS_SUSPENDED},
        "is_closed": status == STATUS_CLOSED,
    }
