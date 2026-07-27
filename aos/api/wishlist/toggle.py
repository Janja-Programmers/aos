"""Race-safe, idempotent wishlist mutation."""

from __future__ import annotations

import frappe

from aos.api.ads.activity import hide_ad_wishlist_activity, record_ad_wishlist_activity
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.ads.api import ads_fail
from aos.services.ads.constants import WISHLIST_TOGGLE_FIELDS
from aos.services.ads.errors import AdsError
from aos.services.ads.validation import ensure_known_fields, normalize_flag, normalize_identifier
from aos.services.wishlist.service import WishlistService

from .constants import WISHLIST_TOGGLE_LIMIT_PER_MINUTE_PER_USER


def toggle_wishlist_impl(**kwargs):
    user, error = require_login()
    if error:
        return error

    limited = rate_limit(
        key=rate_limit_key("wishlist", "toggle", user, request_ip()),
        ttl_seconds=60,
        limit=WISHLIST_TOGGLE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    try:
        ensure_known_fields(kwargs, WISHLIST_TOGGLE_FIELDS)
        ad_id = normalize_identifier(
            kwargs.get("ad_id") or kwargs.get("id"),
            field="ad_id",
            required=True,
        )
        requested = None
        if "wishlisted" in kwargs:
            requested = bool(normalize_flag(kwargs.get("wishlisted"), field="wishlisted"))

        result = WishlistService().set_state(
            user=user,
            ad_id=ad_id,
            requested=requested,
        )
        if result.wishlisted:
            record_ad_wishlist_activity(user=user, ad_id=ad_id)
        else:
            hide_ad_wishlist_activity(user=user, ad_id=ad_id)

        return ok(
            "Wishlist updated.",
            data={
                "ad_id": result.ad_id,
                "wishlisted": result.wishlisted,
                "changed": result.changed,
                "wishlist_count": result.wishlist_count,
            },
        )
    except AdsError as exc:
        return ads_fail(exc, fallback="Invalid wishlist request.")
    except (ValueError, TypeError, frappe.ValidationError):
        return fail(
            "Invalid wishlist request.",
            error="INVALID_WISHLIST_REQUEST",
            http_status=422,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Toggle Wishlist Failed")
        frappe.db.rollback()
        return fail("Failed to update wishlist.", error="INTERNAL_ERROR", http_status=500)
