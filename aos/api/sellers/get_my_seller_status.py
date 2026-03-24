"""
Get My Seller Status.

Lightweight endpoint used for global UI decisions.
- Always succeeds (even if user is not a seller)
- Single DB query (JOIN seller + latest verification)
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import GET_MY_SELLER_STATUS_LIMIT_PER_MINUTE_PER_USER


def get_my_seller_status_impl(**kwargs):
    """Return seller status for current user (optimized single query)."""
    current_user, err = require_login()
    if err:
        return err

    # Rate limit (user-based)
    rl = rate_limit(
        key=f"aos:sellers:get_my_seller_status:{current_user}",
        ttl_seconds=60,
        limit=GET_MY_SELLER_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        result = frappe.db.sql(
            """
            SELECT
                s.name AS seller_id,
                s.status,
                s.is_verified,
                v.status AS verification_status
            FROM `tabAOS Seller` s
            LEFT JOIN `tabAOS Seller Verification` v
                ON v.name = (
                    SELECT name
                    FROM `tabAOS Seller Verification`
                    WHERE seller = s.name
                    ORDER BY creation DESC
                    LIMIT 1
                )
            WHERE s.user = %s
            LIMIT 1
            """,
            (current_user,),
            as_dict=True,
        )

        # Not a seller → valid state (DO NOT fail)
        if not result:
            return ok(
                "User is not a seller.",
                data={
                    "is_seller": False,
                    "seller_id": None,
                    "status": None,
                    "is_verified": False,
                    "verification_status": "Not Submitted",
                },
            )

        row = result[0]

        return ok(
            "Seller status fetched.",
            data={
                "is_seller": True,
                "seller_id": row.get("seller_id"),
                "status": row.get("status"),
                "is_verified": bool(row.get("is_verified")),
                "verification_status": row.get("verification_status") or "Not Submitted",
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
