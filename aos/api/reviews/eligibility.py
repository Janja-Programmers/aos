"""Backward-compatible review eligibility helpers."""

from __future__ import annotations

from typing import Any

from aos.services.reviews.eligibility import get_review_eligibility, public_eligibility_state


def _value(row: Any, key: str):
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def get_review_eligibility_for_ad(*, ad_doc: Any, current_user: str | None) -> dict:
    ad_id = _value(ad_doc, "name")
    if not ad_id:
        return {
            "can_review": False,
            "reason": "AD_NOT_FOUND",
            "has_reviewed": False,
            "has_communicated": False,
        }
    return public_eligibility_state(get_review_eligibility(ad_id=ad_id, reviewer=current_user))


def review_eligibility_error_response(eligibility: dict):
    reason = eligibility.get("reason") or "REVIEW_NOT_ALLOWED"
    messages = {
        "LOGIN_REQUIRED": "Login is required to review this ad.",
        "REVIEW_SELF_NOT_ALLOWED": "You cannot review your own ad.",
        "REVIEW_ALREADY_EXISTS": "You have already reviewed this ad.",
        "TRANSACTION_NOT_ELIGIBLE": "You can only review this ad after contacting the seller.",
        "SELLER_INACTIVE": "Seller is not active.",
        "USER_BLOCKED": "This interaction is blocked.",
    }
    return messages.get(reason, "You cannot review this ad."), reason
