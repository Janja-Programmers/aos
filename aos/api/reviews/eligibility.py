"""Review eligibility helpers."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.communication import has_communication_between_users


REVIEW_REASON_LOGIN_REQUIRED = "LOGIN_REQUIRED"
REVIEW_REASON_AD_NOT_FOUND = "AD_NOT_FOUND"
REVIEW_REASON_SELLER_NOT_FOUND = "SELLER_NOT_FOUND"
REVIEW_REASON_OWN_AD = "OWN_AD"
REVIEW_REASON_ALREADY_REVIEWED = "ALREADY_REVIEWED"
REVIEW_REASON_CONTACT_SELLER_REQUIRED = "CONTACT_SELLER_REQUIRED"
REVIEW_REASON_REVIEW_NOT_ALLOWED = "REVIEW_NOT_ALLOWED"


def _value(row: Any, key: str):
    if not row:
        return None

    if isinstance(row, dict):
        return row.get(key)

    return getattr(row, key, None)


def get_review_eligibility_for_ad(
    *,
    ad_doc: Any,
    current_user: str | None,
) -> dict:
    """
    Return review eligibility for a viewer on a specific ad.

    Caller is responsible for first validating that the ad exists and is visible
    in that endpoint's context.
    """

    if not current_user or current_user == "Guest":
        return {
            "can_review": False,
            "reason": REVIEW_REASON_LOGIN_REQUIRED,
            "has_reviewed": False,
            "has_communicated": False,
        }

    if not ad_doc:
        return {
            "can_review": False,
            "reason": REVIEW_REASON_AD_NOT_FOUND,
            "has_reviewed": False,
            "has_communicated": False,
        }

    ad_name = _value(ad_doc, "name")
    seller = _value(ad_doc, "seller")

    if not ad_name or not seller:
        return {
            "can_review": False,
            "reason": REVIEW_REASON_SELLER_NOT_FOUND,
            "has_reviewed": False,
            "has_communicated": False,
        }

    seller_user = frappe.db.get_value(
        "AOS Seller",
        seller,
        "user",
    )

    if not seller_user:
        return {
            "can_review": False,
            "reason": REVIEW_REASON_SELLER_NOT_FOUND,
            "has_reviewed": False,
            "has_communicated": False,
        }

    if seller_user == current_user:
        return {
            "can_review": False,
            "reason": REVIEW_REASON_OWN_AD,
            "has_reviewed": False,
            "has_communicated": False,
        }

    has_reviewed = bool(
        frappe.db.exists(
            "AOS Review",
            {
                "ad": ad_name,
                "reviewer": current_user,
            },
        )
    )

    if has_reviewed:
        return {
            "can_review": False,
            "reason": REVIEW_REASON_ALREADY_REVIEWED,
            "has_reviewed": True,
            "has_communicated": False,
        }

    has_communicated = has_communication_between_users(
        current_user,
        seller_user,
    )

    if not has_communicated:
        return {
            "can_review": False,
            "reason": REVIEW_REASON_CONTACT_SELLER_REQUIRED,
            "has_reviewed": False,
            "has_communicated": False,
        }

    return {
        "can_review": True,
        "reason": None,
        "has_reviewed": False,
        "has_communicated": True,
    }


def review_eligibility_error_response(eligibility: dict):
    """
    Convert an eligibility result into a stable API error tuple.

    Returns:
        tuple[str, str]: message, code
    """

    reason = eligibility.get("reason")

    if reason == REVIEW_REASON_LOGIN_REQUIRED:
        return "Login is required to review this ad.", REVIEW_REASON_LOGIN_REQUIRED

    if reason == REVIEW_REASON_OWN_AD:
        return "You cannot review your own ad.", REVIEW_REASON_OWN_AD

    if reason == REVIEW_REASON_ALREADY_REVIEWED:
        return "You have already reviewed this ad.", REVIEW_REASON_ALREADY_REVIEWED

    if reason == REVIEW_REASON_CONTACT_SELLER_REQUIRED:
        return (
            "You can only review this ad after contacting the seller.",
            REVIEW_REASON_CONTACT_SELLER_REQUIRED,
        )

    if reason == REVIEW_REASON_SELLER_NOT_FOUND:
        return "Seller not found.", REVIEW_REASON_SELLER_NOT_FOUND

    return "You cannot review this ad.", reason or REVIEW_REASON_REVIEW_NOT_ALLOWED
