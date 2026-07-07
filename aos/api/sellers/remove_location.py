"""
Remove My Seller Location.

Clears the authenticated seller's public map location.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception

from .constants import (
    REMOVE_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
)
from .serializers import serialize_seller_location


LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "display_address",
    "locality",
    "region",
    "country_code",
    "location_name",
    "location_instructions",
    "location_updated_at",
)


def remove_my_seller_location_impl(**kwargs):
    """Remove the authenticated seller's public map location."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=(
            "aos:sellers:remove_location:"
            f"user:{current_user}"
        ),
        ttl_seconds=60,
        limit=(
            REMOVE_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER
        ),
        message=(
            "Too many location removal requests. "
            "Please try again shortly."
        ),
    )
    if rl:
        return rl

    try:
        seller_name = frappe.db.get_value(
            "AOS Seller",
            {
                "user": current_user,
            },
            "name",
        )

        if not seller_name:
            return fail(
                "Seller profile not found.",
                code="NOT_FOUND",
            )

        seller_doc = frappe.get_doc(
            "AOS Seller",
            seller_name,
        )

        if seller_doc.status != "Active":
            return fail(
                "Seller profile is not available.",
                code="VALIDATION_ERROR",
            )

        for fieldname in LOCATION_FIELDS:
            seller_doc.set(
                fieldname,
                None,
            )

        seller_doc.has_location = 0

        seller_doc.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        return ok(
            "Seller location removed successfully.",
            data={
                "seller": seller_doc.name,
                "location": serialize_seller_location(
                    seller_doc
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()

        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

    except Exception:
        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Remove Seller Location Failed",
        )

        return fail(
            "Failed to remove seller location.",
            code="INTERNAL_ERROR",
        )
