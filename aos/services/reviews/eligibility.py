"""Server-derived Reviews eligibility policy for the current AOS business model."""

from __future__ import annotations

import hashlib
from typing import Any

import frappe

from aos.api.shared.blocking import is_blocked_between
from aos.api.shared.communication import get_conversation_between_users, has_communication_between_users

from .constants import ELIGIBILITY_BASIS_COMMUNICATION, REVIEW_DOCTYPE, STATUS_WITHDRAWN
from .errors import ReviewConflictError, ReviewNotFoundError, ReviewPermissionError

REASON_LOGIN_REQUIRED = "LOGIN_REQUIRED"
REASON_AD_NOT_FOUND = "AD_NOT_FOUND"
REASON_SELLER_NOT_FOUND = "SELLER_NOT_FOUND"
REASON_SELLER_INACTIVE = "SELLER_INACTIVE"
REASON_OWN_AD = "REVIEW_SELF_NOT_ALLOWED"
REASON_ALREADY_REVIEWED = "REVIEW_ALREADY_EXISTS"
REASON_CONTACT_REQUIRED = "TRANSACTION_NOT_ELIGIBLE"
REASON_BLOCKED = "USER_BLOCKED"
REASON_ALLOWED = None


_LEGACY_REASON_CODES = {
    REASON_OWN_AD: "OWN_AD",
    REASON_ALREADY_REVIEWED: "ALREADY_REVIEWED",
    REASON_CONTACT_REQUIRED: "CONTACT_SELLER_REQUIRED",
}


def review_key(*, reviewer: str, ad_id: str) -> str:
    material = f"{str(reviewer).strip()}\x1f{str(ad_id).strip()}".encode("utf-8")
    return "review:" + hashlib.sha256(material).hexdigest()


def resolve_ad_target(ad_id: str, *, public_only: bool = True) -> dict[str, Any]:
    fields = ["name", "seller", "status", "title", "average_rating", "total_reviews"]
    ad = frappe.db.get_value("AOS Ad", ad_id, fields, as_dict=True)
    if not ad:
        raise ReviewNotFoundError("Ad not found.", code="REVIEW_TARGET_INVALID")
    if public_only and str(ad.status or "") != "Active":
        raise ReviewNotFoundError("Ad not found.", code="REVIEW_TARGET_INVALID")
    seller = frappe.db.get_value(
        "AOS Seller",
        ad.seller,
        ["name", "user", "status"],
        as_dict=True,
    )
    if not seller:
        raise ReviewNotFoundError("Seller not found.", code="REVIEW_TARGET_INVALID")
    return {"ad": ad, "seller": seller}


def get_review_eligibility(*, ad_id: str, reviewer: str | None) -> dict[str, Any]:
    if not reviewer or reviewer == "Guest":
        return _state(False, REASON_LOGIN_REQUIRED)
    try:
        target = resolve_ad_target(ad_id, public_only=True)
    except ReviewNotFoundError:
        return _state(False, REASON_AD_NOT_FOUND)
    seller = target["seller"]
    seller_user = str(seller.user or "").strip()
    if not seller_user:
        return _state(False, REASON_SELLER_NOT_FOUND)
    if str(seller.status or "") != "Active":
        return _state(False, REASON_SELLER_INACTIVE)
    if seller_user == reviewer:
        return _state(False, REASON_OWN_AD)
    if is_blocked_between(reviewer, seller_user):
        return _state(False, REASON_BLOCKED)

    existing = frappe.db.get_value(
        REVIEW_DOCTYPE,
        {"reviewer": reviewer, "ad": ad_id},
        ["name", "status"],
        as_dict=True,
    )
    if existing:
        return _state(
            False,
            REASON_ALREADY_REVIEWED,
            has_reviewed=True,
            existing_review_id=existing.name,
            existing_review_status=existing.status,
        )

    conversation = get_conversation_between_users(reviewer, seller_user)
    has_communicated = bool(
        conversation and has_communication_between_users(reviewer, seller_user)
    )
    if not has_communicated:
        return _state(False, REASON_CONTACT_REQUIRED)

    return _state(
        True,
        REASON_ALLOWED,
        has_communicated=True,
        eligibility_basis=ELIGIBILITY_BASIS_COMMUNICATION,
        eligibility_reference=conversation,
    )


def enforce_review_eligibility(*, ad_id: str, reviewer: str) -> dict[str, Any]:
    state = get_review_eligibility(ad_id=ad_id, reviewer=reviewer)
    if state["can_review"]:
        return state
    reason = state.get("reason")
    if reason == REASON_ALREADY_REVIEWED:
        public_state = public_eligibility_state(state)
        raise ReviewConflictError(
            "You have already reviewed this ad.",
            code=REASON_ALREADY_REVIEWED,
            data={
                "id": state.get("existing_review_id"),
                "status": state.get("existing_review_status"),
                "review_viewer_state": public_state,
            },
        )
    if reason == REASON_OWN_AD:
        raise ReviewPermissionError("You cannot review your own ad.", code=REASON_OWN_AD)
    if reason == REASON_BLOCKED:
        raise ReviewPermissionError("This interaction is blocked.", code=REASON_BLOCKED)
    if reason == REASON_SELLER_INACTIVE:
        raise ReviewPermissionError("Seller is not active.", code=REASON_SELLER_INACTIVE)
    if reason == REASON_CONTACT_REQUIRED:
        raise ReviewPermissionError(
            "You can only review this ad after contacting the seller.",
            code=REASON_CONTACT_REQUIRED,
        )
    if reason in {REASON_AD_NOT_FOUND, REASON_SELLER_NOT_FOUND}:
        raise ReviewNotFoundError("Review target not found.", code="REVIEW_TARGET_INVALID")
    raise ReviewPermissionError("You cannot review this ad.", code="REVIEW_NOT_ALLOWED")


def _state(
    can_review: bool,
    reason: str | None,
    *,
    has_reviewed: bool = False,
    has_communicated: bool = False,
    existing_review_id: str | None = None,
    existing_review_status: str | None = None,
    eligibility_basis: str | None = None,
    eligibility_reference: str | None = None,
) -> dict[str, Any]:
    return {
        "can_review": bool(can_review),
        "reason": reason,
        "has_reviewed": bool(has_reviewed),
        "has_communicated": bool(has_communicated),
        "existing_review_id": existing_review_id,
        "existing_review_status": existing_review_status,
        "eligibility_basis": eligibility_basis,
        # The conversation identifier is private evidence and must never be sent
        # by public serializers. Services consume it only for persistence/audit.
        "_eligibility_reference": eligibility_reference,
    }


def public_eligibility_state(state: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in state.items() if not key.startswith("_")}
    canonical_reason = result.get("reason")
    result["reason_code"] = canonical_reason
    result["reason"] = _LEGACY_REASON_CODES.get(canonical_reason, canonical_reason)
    return result
