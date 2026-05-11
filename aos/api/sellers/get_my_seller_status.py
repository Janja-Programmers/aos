"""
Get My Seller Status.

Lightweight endpoint used for seller-specific UI decisions.
- Always succeeds even if user is not a seller
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import GET_MY_SELLER_STATUS_LIMIT_PER_MINUTE_PER_USER


def get_my_seller_status_impl(**kwargs):
    """Return seller status for current user."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:sellers:get_my_seller_status:{current_user}",
        ttl_seconds=60,
        limit=GET_MY_SELLER_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        seller = frappe.db.get_value(
            "AOS Seller",
            {"user": current_user},
            [
                "name",
                "status",
                "seller_type",
            ],
            as_dict=True,
        )

        if not seller:
            return ok(
                "User is not a seller.",
                data={
                    "is_seller": False,
                    "seller_id": None,
                    "status": None,
                    "seller_type": None
                },
            )

        return ok(
            "Seller status fetched.",
            data={
                "is_seller": True,
                "seller_id": seller.name,
                "status": seller.status,
                "seller_type": seller.seller_type
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get My Seller Status Failed",
        )
        return fail(
            "Failed to fetch seller status.",
            code="INTERNAL_ERROR",
        )
