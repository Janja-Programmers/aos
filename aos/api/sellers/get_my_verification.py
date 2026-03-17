"""
Get My Seller Verification Status.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.responses import fail, ok


def get_my_verification_impl(**kwargs):
    """Fetch logged-in seller verification status."""

    current_user, err = require_login()
    if err:
        return err

    try:

        seller = frappe.db.get_value(
            "AOS Seller",
            {"user": current_user},
            "name"
        )

        if not seller:
            return fail(
                "Seller profile not found.",
                code="NOT_FOUND"
            )

        verification = frappe.db.get_value(
            "AOS Seller Verification",
            {"seller": seller},
            [
                "name",
                "status",
                "verified_on",
                "rejection_reason"
            ],
            as_dict=True
        )

        seller_doc = frappe.get_doc("AOS Seller", seller)

        # Remove rejection reason unless rejected
        if verification and verification.get("status") != "Rejected":
            verification.pop("rejection_reason", None)

        return ok(
            "Verification status fetched.",
            data={
                "is_verified": bool(seller_doc.is_verified),
                "verification": verification
            }
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Verification Failed"
        )
        return fail(
            "Failed to fetch verification status.",
            code="INTERNAL_ERROR"
        )
