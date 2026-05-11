"""
Verification validators.
"""

from __future__ import annotations

import frappe


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


def validate_verification_documents(documents: list):
    """Validate verification documents."""

    if not documents:
        frappe.throw("Verification documents are required.")

    for d in documents:
        attachment = d.get("attachment")

        if not attachment:
            frappe.throw(
                "Each verification document must include an attachment."
            )

        if not frappe.db.exists(
            "File",
            {"file_url": attachment},
        ):
            frappe.throw(
                f"Verification document file does not exist: {attachment}"
            )
