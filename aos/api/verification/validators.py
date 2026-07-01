"""
Verification validators.
"""

from __future__ import annotations

import frappe

from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaValidationError,
)

from .media import normalize_verification_documents_for_submit


ALLOWED_VERIFICATION_TYPES = [
    "Business",
    "Individual",
]


def validate_verification_type(verification_type: str):
    """Validate verification type."""

    if not verification_type:
        frappe.throw("Verification type is required.")

    if verification_type not in ALLOWED_VERIFICATION_TYPES:
        frappe.throw("Invalid verification type.")


def validate_business_verification(kwargs: dict):
    """Validate business verification fields."""

    required_fields = {
        "business_name": "Business name",
        "business_type": "Business type",
        "business_category": "Business category",
        "business_phone_number": "Business phone number",
        "business_email": "Business email",
        "business_address": "Business address",
    }

    for fieldname, label in required_fields.items():
        value = kwargs.get(fieldname)

        if not value:
            frappe.throw(f"{label} is required.")


def validate_individual_verification(kwargs: dict):
    """Validate individual verification fields."""

    required_fields = {
        "legal_name": "Legal name",
        "phone_number": "Phone number",
    }

    for fieldname, label in required_fields.items():
        value = kwargs.get(fieldname)

        if not value:
            frappe.throw(f"{label} is required.")


def validate_verification_documents(documents: list, *, user: str | None = None):
    """Validate and normalize verification documents.

    New verification submissions use private MinIO-backed media objects instead
    of Frappe File URLs. The caller should pass the current user so ownership,
    purpose, and status can be enforced before the request is saved.
    """

    if not user:
        frappe.throw("User is required for verification document validation.")

    try:
        return normalize_verification_documents_for_submit(
            user=user,
            documents=documents,
        )
    except (MediaNotFoundError, MediaPermissionError, MediaValidationError) as exc:
        frappe.throw(str(exc))
