"""Server-derived Reviews eligibility policy for the current AOS business model."""

from __future__ import annotations

import hashlib
from typing import Any

import frappe

from aos.api.shared.communication import get_conversation_between_users, has_communication_between_users
from aos.services.ads.errors import AdsNotFoundError
from aos.services.ads.visibility import require_public_ad_for_viewer

from .constants import ELIGIBILITY_BASIS_COMMUNICATION, REVIEW_DOCTYPE
from .errors import ReviewConflictError, ReviewNotFoundError, ReviewPermissionError

REASON_LOGIN_REQUIRED = "LOGIN_REQUIRED"
REASON_AD_NOT_FOUND = "AD_NOT_FOUND"
REASON_OWN_AD = "REVIEW_SELF_NOT_ALLOWED"
REASON_ALREADY_REVIEWED = "REVIEW_ALREADY_EXISTS"
REASON_CONTACT_REQUIRED = "TRANSACTION_NOT_ELIGIBLE"
REASON_ALLOWED = None


def review_key(*, reviewer: str, ad_id: str) -> str:
    """Return the durable single-review-per-account/ad domain key."""

    material = f"{str(reviewer).strip()}\x1f{str(ad_id).strip()}".encode("utf-8")
    return "review:" + hashlib.sha256(material).hexdigest()


def resolve_ad_target(
    public_ad_id: str,
    *,
    viewer: str,
) -> dict[str, Any]:
    """Resolve a public Ad through the hardened Ads visibility boundary."""

    try:
        visible = require_public_ad_for_viewer(public_id=public_ad_id, viewer=viewer or "Guest")
    except AdsNotFoundError as exc:
        raise ReviewNotFoundError("Ad not found.", code="REVIEW_TARGET_INVALID") from exc

    ad = frappe.db.get_value(
        "AOS Ad",
        visible.name,
        ["name", "public_id", "seller", "title", "average_rating", "total_reviews"],
        as_dict=True,
    )
    if not ad:
        raise ReviewNotFoundError("Ad not found.", code="REVIEW_TARGET_INVALID")
    seller = frappe.db.get_value(
        "AOS Seller",
        ad.seller,
        ["name", "public_id", "user", "status"],
        as_dict=True,
    )
    if not seller:
        raise ReviewNotFoundError("Ad not found.", code="REVIEW_TARGET_INVALID")
    return {"ad": ad, "seller": seller}


def get_review_eligibility(*, ad_id: str, reviewer: str | None) -> dict[str, Any]:
    """Return canonical eligibility for a public Ad identifier."""

    if not reviewer or reviewer == "Guest":
        return _state(False, REASON_LOGIN_REQUIRED)
    try:
        target = resolve_ad_target(ad_id, viewer=reviewer)
    except ReviewNotFoundError:
        return _state(False, REASON_AD_NOT_FOUND)

    ad = target["ad"]
    seller_user = str(target["seller"].user or "").strip()
    if seller_user == reviewer:
        return _state(False, REASON_OWN_AD)

    existing = frappe.db.get_value(
        REVIEW_DOCTYPE,
        {"reviewer": reviewer, "ad": ad.name},
        ["public_id", "status"],
        as_dict=True,
    )
    if existing:
        return _state(
            False,
            REASON_ALREADY_REVIEWED,
            has_reviewed=True,
            existing_review_id=existing.public_id,
            existing_review_status=existing.status,
        )

    conversation = get_conversation_between_users(reviewer, seller_user)
    has_communicated = bool(conversation and has_communication_between_users(reviewer, seller_user))
    if not has_communicated:
        return _state(False, REASON_CONTACT_REQUIRED)

    return _state(
        True,
        REASON_ALLOWED,
        has_communicated=True,
        eligibility_basis=ELIGIBILITY_BASIS_COMMUNICATION,
        eligibility_reference=conversation,
        internal_ad_name=ad.name,
    )


def enforce_review_eligibility(*, ad_id: str, reviewer: str) -> dict[str, Any]:
    state = get_review_eligibility(ad_id=ad_id, reviewer=reviewer)
    if state["can_review"]:
        return state
    reason = state.get("reason")
    if reason == REASON_ALREADY_REVIEWED:
        raise ReviewConflictError(
            "You have already reviewed this ad.",
            code=REASON_ALREADY_REVIEWED,
            data={
                "review_id": state.get("existing_review_id"),
                "status": state.get("existing_review_status"),
                "review_viewer_state": public_eligibility_state(state),
            },
        )
    if reason == REASON_OWN_AD:
        raise ReviewPermissionError("You cannot review your own ad.", code=REASON_OWN_AD)
    if reason == REASON_CONTACT_REQUIRED:
        raise ReviewPermissionError(
            "You can only review this ad after contacting the seller.",
            code=REASON_CONTACT_REQUIRED,
        )
    if reason == REASON_AD_NOT_FOUND:
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
    internal_ad_name: str | None = None,
) -> dict[str, Any]:
    return {
        "can_review": bool(can_review),
        "reason": reason,
        "has_reviewed": bool(has_reviewed),
        "has_communicated": bool(has_communicated),
        "existing_review_id": existing_review_id,
        "existing_review_status": existing_review_status,
        "eligibility_basis": eligibility_basis,
        "_eligibility_reference": eligibility_reference,
        "_ad_name": internal_ad_name,
    }


def public_eligibility_state(state: dict[str, Any]) -> dict[str, Any]:
    """Strip private evidence and return one current eligibility contract."""

    return {key: value for key, value in state.items() if not key.startswith("_")}
