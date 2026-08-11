"""Compatibility adapters for the centralized Verification validator."""

from __future__ import annotations

import frappe

from aos.services.verification.constants import VERIFICATION_TYPES
from aos.services.verification.errors import VerificationValidationError
from aos.api.verification.media import normalize_verification_documents_for_submit
from aos.services.verification.validation import normalize_submit_payload

ALLOWED_VERIFICATION_TYPES = sorted(VERIFICATION_TYPES)


def validate_verification_type(verification_type: str):
    try:
        normalize_submit_payload(
            {
                "verification_type": verification_type,
                "verification_documents": [{"document_type": "compat", "media_id": "MEDIA-COMPAT"}],
                **(
                    {"legal_name": "Compatibility User", "phone_number": "+254700000000"}
                    if verification_type == "Individual"
                    else {
                        "business_name": "Compatibility Business",
                        "business_type": "Sole Proprietorship",
                        "business_category": "Compatibility",
                        "business_phone_number": "+254700000000",
                        "business_email": "compat@example.com",
                        "business_address": "Compatibility",
                    }
                ),
            }
        )
    except VerificationValidationError as exc:
        if "document media" not in str(exc).lower():
            frappe.throw(str(exc), frappe.ValidationError)


def validate_business_verification(kwargs: dict):
    payload = dict(kwargs or {})
    payload["verification_type"] = "Business"
    payload.setdefault("verification_documents", [{"document_type": "compat", "media_id": "MEDIA-COMPAT"}])
    try:
        normalize_submit_payload(payload)
    except VerificationValidationError as exc:
        if "document media" not in str(exc).lower():
            frappe.throw(str(exc), frappe.ValidationError)


def validate_individual_verification(kwargs: dict):
    payload = dict(kwargs or {})
    payload["verification_type"] = "Individual"
    payload.setdefault("verification_documents", [{"document_type": "compat", "media_id": "MEDIA-COMPAT"}])
    try:
        normalize_submit_payload(payload)
    except VerificationValidationError as exc:
        if "document media" not in str(exc).lower():
            frappe.throw(str(exc), frappe.ValidationError)


def validate_verification_documents(documents: list, *, user: str | None = None):
    """Legacy adapter around the strict document/media boundary."""
    if not user:
        frappe.throw("User is required for verification document validation.", frappe.ValidationError)
    try:
        return normalize_verification_documents_for_submit(
            user=user,
            documents=[dict(row or {}) for row in (documents or [])],
        )
    except VerificationValidationError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
    except Exception:
        # Do not leak Media existence/ownership details through a compatibility
        # validator that older callers may expose directly.
        frappe.throw("Invalid verification document media.", frappe.ValidationError)
