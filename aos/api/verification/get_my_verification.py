"""
Get My Verification Status.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import GET_MY_VERIFICATION_LIMIT_PER_MINUTE_PER_USER
from .media import serialize_verification_document


def get_my_verification_impl(**kwargs):
    """Fetch logged-in user's verification status."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:verification:get_my:user:{current_user}",
        ttl_seconds=60,
        limit=GET_MY_VERIFICATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        profile = frappe.db.get_value(
            "AOS Profile",
            current_user,
            ["is_verified", "verified_by", "verified_on"],
            as_dict=True,
        )

        if not profile:
            return fail(
                "Profile not found.",
                error="PROFILE_NOT_FOUND",
            )

        verification = frappe.db.get_value(
            "AOS Verification Request",
            {"user": current_user},
            [
                "name",
                "verification_type",
                "status",
                "verified_on",
                "rejection_reason",
            ],
            order_by="creation desc",
            as_dict=True,
        )

        if verification:
            request_doc = frappe.get_doc("AOS Verification Request", verification.name)
            verification["documents"] = [
                serialize_verification_document(
                    row,
                    user=current_user,
                    include_url=False,
                )
                for row in request_doc.verification_documents
            ]

            if verification.get("status") != "Rejected":
                verification.pop("rejection_reason", None)

        return ok(
            "Verification status fetched.",
            data={
                "is_verified": bool(profile.is_verified),
                "verified_by": profile.verified_by,
                "verified_on": profile.verified_on,
                "verification": verification,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Verification Failed",
        )
        return fail(
            "Failed to fetch verification status.",
            error="INTERNAL_ERROR",
        )
