"""
Get Seller Location.

Behavior:
- When seller is omitted, return the authenticated user's seller location.
- When seller is provided, return that active seller's public location.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import (
    current_user,
    require_login,
)
from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display

from .constants import (
    GET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
    GET_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_IP,
)
from .serializers import serialize_seller_location


LOCATION_FIELDS = [
    "name",
    "user",
    "status",
    "has_location",
    "location_name",
    "location_instructions",
    "latitude",
    "longitude",
    "display_address",
    "locality",
    "region",
    "country_code",
    "location_updated_at",
]


def get_seller_location_impl(**kwargs):
    """
    Fetch a seller location.

    Rules:
    - If seller is omitted, login is required and the authenticated
      user's seller location is returned.
    - If seller is supplied, the active seller's public location is
      returned.
    """

    seller_name = _normalize_optional_string(
        kwargs.get("seller")
    )

    if not seller_name:
        return _get_current_seller_location()

    return _get_public_seller_location(
        seller_name=seller_name,
    )


def _get_current_seller_location():
    """Fetch the authenticated user's seller location."""

    current_user_value, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            "aos:sellers:get_location:"
            f"user:{current_user_value}"
        ),
        ttl_seconds=60,
        limit=(
            GET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        seller = frappe.db.get_value(
            "AOS Seller",
            {
                "user": current_user_value,
            },
            LOCATION_FIELDS,
            as_dict=True,
        )

        if not seller:
            return fail(
                "Seller profile not found.",
                error="NOT_FOUND",
            )

        return ok(
            "Seller location fetched successfully.",
            data={
                "seller": seller.get("name"),
                "user": get_user_display(seller.get("user")).get("user"),
                "is_owner": True,
                "location": serialize_seller_location(
                    seller
                ),
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Own Seller Location Failed",
        )

        return fail(
            "Failed to fetch seller location.",
            error="INTERNAL_ERROR",
        )


def _get_public_seller_location(
    *,
    seller_name: str,
):
    """Fetch an active seller's public location."""

    ip = request_ip()

    rl = rate_limit(
        key=f"aos:sellers:get_location:ip:{ip}",
        ttl_seconds=60,
        limit=(
            GET_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_IP
        ),
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        seller = frappe.db.get_value(
            "AOS Seller",
            {
                "name": seller_name,
                "status": "Active",
            },
            LOCATION_FIELDS,
            as_dict=True,
        )

        if not seller:
            return fail(
                "Seller not found.",
                error="NOT_FOUND",
            )

        viewer = current_user()

        is_owner = bool(
            viewer
            and viewer != "Guest"
            and viewer == seller.get("user")
        )

        return ok(
            "Seller location fetched successfully.",
            data={
                "seller": seller.get("name"),
                "user": get_user_display(seller.get("user")).get("user"),
                "is_owner": is_owner,
                "location": serialize_seller_location(
                    seller
                ),
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Seller Location Failed",
        )

        return fail(
            "Failed to fetch seller location.",
            error="INTERNAL_ERROR",
        )


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim an optional string and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
