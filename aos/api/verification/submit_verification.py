"""
Submit or Resubmit Verification Request.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from aos.services.account_service import get_or_create_seller

from .constants import SUBMIT_VERIFICATION_LIMIT_PER_MINUTE_PER_USER
from .validators import (
    validate_business_verification,
    validate_individual_verification,
    validate_verification_documents,
    validate_verification_type,
)


def submit_verification_impl(**kwargs):
    """Create or resubmit verification request."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:verification:submit:user:{current_user}",
        ttl_seconds=60,
        limit=SUBMIT_VERIFICATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        if not frappe.db.exists("AOS Profile", current_user):
            return fail(
                "Profile not found.",
                code="PROFILE_NOT_FOUND",
            )

        profile = frappe.get_doc("AOS Profile", current_user)

        if profile.is_verified:
            return fail(
                "Account is already verified.",
                code="VALIDATION_ERROR",
            )

        verification_type = kwargs.get("verification_type")

        validate_verification_type(verification_type)

        if verification_type == "Business":
            validate_business_verification(kwargs)

        elif verification_type == "Individual":
            validate_individual_verification(kwargs)

        documents = kwargs.get("verification_documents") or []

        validate_verification_documents(documents)

        verification_name = frappe.db.get_value(
            "AOS Verification Request",
            {"user": current_user},
            "name",
        )

        if not verification_name:
            verification = frappe.new_doc("AOS Verification Request")
            verification.user = current_user

        else:
            verification = frappe.get_doc(
                "AOS Verification Request",
                verification_name,
            )

            if verification.status in ["Pending", "Reviewing"]:
                return fail(
                    "Verification request already in progress.",
                    code="VALIDATION_ERROR",
                )

            if verification.status not in ["Rejected", "Revoked"]:
                return fail(
                    "Verification request cannot be submitted.",
                    code="VALIDATION_ERROR",
                )

            verification.rejection_reason = None
            verification.status = "Pending"
            verification.set("verification_documents", [])

        verification.verification_type = verification_type

        # INDIVIDUAL
        if verification_type == "Individual":
            verification.legal_name = kwargs.get("legal_name")
            verification.phone_number = kwargs.get("phone_number")

            # Clear business fields
            verification.business_name = None
            verification.business_type = None
            verification.business_category = None
            verification.business_phone_number = None
            verification.business_email = None
            verification.business_website = None
            verification.business_address = None

        # BUSINESS
        elif verification_type == "Business":
            seller = get_or_create_seller(current_user)

            verification.business_name = kwargs.get("business_name")
            verification.business_type = kwargs.get("business_type")
            verification.business_category = kwargs.get("business_category")
            verification.business_phone_number = kwargs.get("business_phone_number")
            verification.business_email = kwargs.get("business_email")
            verification.business_website = kwargs.get("business_website")
            verification.business_address = kwargs.get("business_address")

            # Clear individual fields
            verification.legal_name = None
            verification.phone_number = None

        # DOCUMENTS
        for d in documents:
            verification.append(
                "verification_documents",
                {
                    "document_type": d.get("document_type"),
                    "document_number": d.get("document_number"),
                    "issue_date": d.get("issue_date"),
                    "expiry_date": d.get("expiry_date"),
                    "attachment": d.get("attachment"),
                },
            )

        verification.save(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Verification request submitted successfully.",
            data={
                "verification_type": verification.verification_type,
                "status": verification.status,
            },
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Submit Verification Failed",
        )

        frappe.db.rollback()

        return fail(
            "Failed to submit verification.",
            code="INTERNAL_ERROR",
        )
