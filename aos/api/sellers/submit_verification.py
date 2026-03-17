"""
Submit or Resubmit Seller Verification.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import SUBMIT_VERIFICATION_LIMIT_PER_MINUTE_PER_USER


def _file_exists(file_url: str) -> bool:
    """Check if a file exists in the File doctype."""
    if not file_url:
        return False

    return bool(
        frappe.db.exists(
            "File",
            {"file_url": file_url}
        )
    )


def submit_verification_impl(**kwargs):
    """Create or resubmit seller verification."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:sellers:submit_verification:user:{current_user}",
        ttl_seconds=60,
        limit=SUBMIT_VERIFICATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

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

        seller_doc = frappe.get_doc("AOS Seller", seller)

        if seller_doc.is_verified:
            return fail(
                "Seller is already verified.",
                code="VALIDATION_ERROR"
            )

        verification_name = frappe.db.get_value(
            "AOS Seller Verification",
            {"seller": seller},
            "name"
        )

        # CREATE OR LOAD VERIFICATION
        if not verification_name:
            verification = frappe.new_doc("AOS Seller Verification")
            verification.seller = seller

        else:
            verification = frappe.get_doc(
                "AOS Seller Verification",
                verification_name
            )

            # Prevent duplicate active requests
            if verification.status in ["Pending", "Reviewing"]:
                return fail(
                    "Verification request already in progress.",
                    code="VALIDATION_ERROR"
                )

            # Only rejected verifications can be resubmitted
            if verification.status != "Rejected":
                return fail(
                    "Verification cannot be submitted.",
                    code="VALIDATION_ERROR"
                )

            # Reset rejection state
            verification.rejection_reason = None
            verification.status = "Pending"

            # Replace documents completely
            verification.set("verification_documents", [])

        # VALIDATE BUSINESS FIELDS
        business_name = kwargs.get("business_name")
        business_type = kwargs.get("business_type")
        business_category = kwargs.get("business_category")
        business_phone_number = kwargs.get("business_phone_number")
        business_email = kwargs.get("business_email")
        physical_address = kwargs.get("physical_address")

        if not business_name:
            return fail("Business name is required.", code="VALIDATION_ERROR")

        if not business_type:
            return fail("Business type is required.", code="VALIDATION_ERROR")

        if not business_category:
            return fail("Business category is required.", code="VALIDATION_ERROR")

        if not business_phone_number:
            return fail("Business phone number is required.", code="VALIDATION_ERROR")

        if not business_email:
            return fail("Business email is required.", code="VALIDATION_ERROR")

        if not physical_address:
            return fail("Physical address is required.", code="VALIDATION_ERROR")

        # UPDATE BUSINESS DETAILS
        verification.business_name = business_name
        verification.business_type = business_type
        verification.business_category = business_category
        verification.business_phone_number = business_phone_number
        verification.business_email = business_email
        verification.business_website = kwargs.get("business_website")
        verification.physical_address = physical_address

        # ADD DOCUMENTS
        documents = kwargs.get("verification_documents") or []

        if not documents:
            return fail(
                "Verification documents are required.",
                code="VALIDATION_ERROR"
            )

        for d in documents:
            attachment = d.get("attachment")

            if not attachment:
                return fail(
                    "Each verification document must include an attachment.",
                    code="VALIDATION_ERROR"
                )

            if not _file_exists(attachment):
                return fail(
                    f"Verification document file does not exist: {attachment}",
                    code="VALIDATION_ERROR"
                )

            verification.append(
                "verification_documents",
                {
                    "document_type": d.get("document_type"),
                    "document_number": d.get("document_number"),
                    "issue_date": d.get("issue_date"),
                    "expiry_date": d.get("expiry_date"),
                    "attachment": attachment,
                },
            )

        verification.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Verification request submitted successfully.",
            data={
                "status": verification.status
            }
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Submit Verification Failed"
        )
        return fail(
            "Failed to submit verification.",
            code="INTERNAL_ERROR"
        )
