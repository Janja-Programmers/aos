"""
Live Stream validators.

Reusable validation helpers for live feature.
"""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail


# FETCH HELPERS
def get_live_row(live_id: str):
    try:
        return frappe.get_doc("AOS Live Stream", live_id)
    except frappe.DoesNotExistError:
        return None


def get_live_view_row(view_id: str):
    try:
        return frappe.get_doc("AOS Live Stream View", view_id)
    except frappe.DoesNotExistError:
        return None


def get_ad_row(ad_id: str):
    try:
        return frappe.get_doc("AOS Ad", ad_id)
    except frappe.DoesNotExistError:
        return None


def get_comment_row(comment_id: str):
    try:
        return frappe.get_doc("AOS Live Stream Comment", comment_id)
    except frappe.DoesNotExistError:
        return None


# LIVE VALIDATION
def validate_live_exists(live_id: str):
    live = get_live_row(live_id)

    if not live:
        return None, fail("Live stream not found.", code="NOT_FOUND")

    return live, None


def validate_live_active(live):
    if live.status != "live":
        return fail("Live stream is not active.", code="INVALID_STATE")

    return None


def validate_live_not_ended(live):
    if live.status == "ended":
        return fail("Live stream has ended.", code="INVALID_STATE")

    return None


# USER / SELLER VALIDATION
def validate_user_is_seller(live, user: str):
    if live.seller != user:
        return fail("Only seller can perform this action.", code="PERMISSION_DENIED")

    return None

def validate_seller_can_go_live(seller_id: str):
    seller = frappe.db.get_value(
        "AOS Seller",
        seller_id,
        ["status", "is_verified", "user"],
        as_dict=True,
    )

    if not seller:
        return None, fail("Seller not found.", code="NOT_FOUND")

    if seller.status != "Active":
        return None, fail("Seller account is not active.", code="INVALID_STATE")

    total_followers = (
        frappe.db.get_value(
            "AOS Profile",
            seller.user,
            "total_followers",
        )
        or 0
    )

    if not seller.get("is_verified") and total_followers < 1000:
        return None, fail(
            "You must be verified or have at least 1,000 followers to go live.",
            code="NOT_ELIGIBLE",
        )

    seller["total_followers"] = total_followers

    return seller, None

# VIEW VALIDATION
def validate_view_identity(user: str | None, session_id: str | None):
    if not user and not session_id:
        return fail("Either user or session_id is required.", code="VALIDATION_ERROR")

    return None


def validate_no_active_view_session(live_id: str, user: str | None, session_id: str | None):
    filters = {
        "live_stream": live_id,
        "is_active": 1,
    }

    if user:
        filters["user"] = user
    else:
        filters["session_id"] = session_id

    exists = frappe.db.exists("AOS Live Stream View", filters)

    if exists:
        return fail("Active view session already exists.", code="INVALID_STATE")

    return None


def validate_active_view_session(live_id: str, user: str | None, session_id: str | None):
    filters = {
        "live_stream": live_id,
        "is_active": 1,
    }

    if user:
        filters["user"] = user
    else:
        filters["session_id"] = session_id

    view = frappe.db.get_value(
        "AOS Live Stream View",
        filters,
        ["name"],
        as_dict=True,
    )

    if not view:
        return None, fail("Active session not found.", code="NOT_FOUND")

    return view, None


# COMMENT VALIDATION
def validate_comment_exists(comment_id: str):
    comment = get_comment_row(comment_id)

    if not comment:
        return None, fail("Comment not found.", code="NOT_FOUND")

    return comment, None


def validate_comment_belongs_to_live(comment, live_id: str):
    if comment.live_stream != live_id:
        return fail("Comment does not belong to this live stream.", code="INVALID_STATE")

    return None


# AD VALIDATION
def validate_ad_exists(ad_id: str):
    ad = get_ad_row(ad_id)

    if not ad:
        return None, fail("Ad not found.", code="NOT_FOUND")

    return ad, None


def validate_ad_active(ad):
    if ad.status != "Active":
        return fail("Ad is not active.", code="INVALID_STATE")

    return None


def validate_ad_belongs_to_seller(ad, live):
    if ad.seller != live.seller:
        return fail("Ad must belong to the seller.", code="PERMISSION_DENIED")

    return None


def validate_ad_same_country(ad, live):
    if ad.country != live.country:
        return fail("Ad must match live stream country.", code="INVALID_STATE")

    return None


def validate_ad_not_already_attached(live_id: str, ad_id: str):
    exists = frappe.db.exists(
        "AOS Live Stream Ad",
        {
            "live_stream": live_id,
            "ad": ad_id,
        },
    )

    if exists:
        return fail("Ad already attached to this live stream.", code="INVALID_STATE")

    return None
