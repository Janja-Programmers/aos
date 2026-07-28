"""Canonical Seller ownership, capability, and lifecycle policy."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from .constants import (
    SELLER_DOCTYPE,
    SELLER_STATUSES,
    SELLER_TYPE_BUSINESS,
    SELLER_TYPE_INDIVIDUAL,
    STATUS_ACTIVE,
    STATUS_DELETED,
    STATUS_REASON_CODE_MAX_LENGTH,
    STATUS_SOURCE_MAX_LENGTH,
    STATUS_SUSPENDED,
)
from .errors import (
    SellerNotFoundError,
    SellerPermissionError,
    SellerStateError,
    SellerValidationError,
)
from .observability import seller_log
from .validation import normalize_business_category

_ALLOWED_TRANSITIONS = {
    STATUS_ACTIVE: frozenset({STATUS_SUSPENDED, STATUS_DELETED}),
    STATUS_SUSPENDED: frozenset({STATUS_ACTIVE, STATUS_DELETED}),
    STATUS_DELETED: frozenset(),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def get_seller_for_user(
    user: str,
    *,
    require_active: bool = False,
    fields: list[str] | None = None,
):
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
    row = frappe.db.get_value(
        SELLER_DOCTYPE,
        {"user": clean_user},
        selected,
        as_dict=True,
    )
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
    """Return the user's Seller record or create one safely.

    Creation is idempotent under the unique ``user`` constraint. Existing
    Suspended/Deleted sellers are returned without reactivation; callers must
    apply explicit lifecycle policy instead of silently enabling a seller.
    """

    clean_user = _clean(user)
    if not clean_user or clean_user == "Guest":
        return None
    existing = frappe.db.get_value(SELLER_DOCTYPE, {"user": clean_user}, "name")
    if existing:
        return frappe.get_doc(SELLER_DOCTYPE, existing)
    if not frappe.db.exists("User", clean_user):
        raise SellerValidationError("User does not exist.", code="ACCOUNT_NOT_FOUND", http_status=404)
    doc = frappe.get_doc(
        {
            "doctype": SELLER_DOCTYPE,
            "user": clean_user,
            "seller_type": SELLER_TYPE_INDIVIDUAL,
            "status": STATUS_ACTIVE,
            "status_reason_code": "SELLER_CREATED",
            "status_source": "seller_service",
            "status_changed_at": now_datetime(),
            "storefront_version": 0,
        }
    )
    doc.flags.aos_seller_lifecycle_action = "create"
    try:
        doc.insert(ignore_permissions=True)
        seller_log("seller.status.changed", seller_id=doc.name, status=STATUS_ACTIVE, operation="create")
        return doc
    except Exception:
        # A concurrent request may have won the unique-user insert.
        existing = frappe.db.get_value(SELLER_DOCTYPE, {"user": clean_user}, "name")
        if existing:
            return frappe.get_doc(SELLER_DOCTYPE, existing)
        raise


def set_seller_status(
    seller_id: Any,
    *,
    status: str,
    reason_code: str,
    source: str,
    actor: str | None = None,
    allow_reactivate_deleted: bool = False,
):
    """Apply an explicit, locked Seller lifecycle transition.

    This function does not commit. Callers participate in the outer request or
    job transaction.
    """

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

    locked = frappe.db.sql(
        "SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE",
        (clean_id,),
        as_dict=True,
    )
    if not locked:
        raise SellerNotFoundError("Seller not found.")
    doc = frappe.get_doc(SELLER_DOCTYPE, clean_id)
    old_status = _clean(doc.status)
    if old_status == new_status:
        return doc, False
    if old_status == STATUS_DELETED and new_status == STATUS_ACTIVE and allow_reactivate_deleted:
        pass
    elif new_status not in _ALLOWED_TRANSITIONS.get(old_status, frozenset()):
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
    seller_log(
        "seller.status.changed",
        seller_id=doc.name,
        status=new_status,
        operation=source_value,
        outcome="success",
    )
    return doc, True


def sync_verified_business_profile(
    *,
    user: str,
    business_category: Any,
    source: str = "verification",
):
    """Project an approved business-verification decision onto Seller.

    Verification owns the decision. Seller owns its type/category fields and
    validates the projection. This function does not change seller status.
    """

    row = get_seller_for_user(user)
    if not row:
        raise SellerNotFoundError("Seller profile not found.")
    if _clean(row.status) == STATUS_DELETED:
        raise SellerStateError("Seller profile is unavailable.", code="SELLER_INACTIVE")
    category = normalize_business_category(business_category)
    if not category:
        raise SellerValidationError("Business category is required.", code="INVALID_BUSINESS_CATEGORY")
    doc = frappe.get_doc(SELLER_DOCTYPE, row.name)
    doc.seller_type = SELLER_TYPE_BUSINESS
    doc.business_category = category
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
        "requires_reactivation": status == STATUS_DELETED,
    }
