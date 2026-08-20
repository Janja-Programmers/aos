"""Authoritative constants for the existing AOS Verification model."""

from __future__ import annotations

VERIFICATION_DOCTYPE = "AOS Verification Request"
VERIFICATION_DOCUMENT_DOCTYPE = "AOS Verification Document"
VERIFICATION_DOCUMENT_PURPOSE = "verification_document"

TYPE_BUSINESS = "Business"
TYPE_INDIVIDUAL = "Individual"
VERIFICATION_TYPES = frozenset({TYPE_BUSINESS, TYPE_INDIVIDUAL})

STATUS_PENDING = "Pending"
STATUS_REVIEWING = "Reviewing"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_REVOKED = "Revoked"
VERIFICATION_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_REVIEWING, STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED}
)

REVIEW_STATUSES = frozenset({STATUS_REVIEWING, STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED})
RESUBMIT_FROM_STATUSES = frozenset({STATUS_REJECTED, STATUS_REVOKED})

# Existing Desk lifecycle, made explicit. Rejected/Revoked are reopened only by
# the canonical resubmission service, not by arbitrary Desk field editing.
REVIEWER_TRANSITIONS = {
    STATUS_PENDING: frozenset({STATUS_REVIEWING, STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED}),
    STATUS_REVIEWING: frozenset({STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED}),
    STATUS_APPROVED: frozenset({STATUS_REVOKED}),
    STATUS_REJECTED: frozenset(),
    STATUS_REVOKED: frozenset(),
}

BUSINESS_TYPES = frozenset(
    {"Sole Proprietorship", "Partnership", "Limited Company", "Corporation"}
)

TRANSPORT_FIELDS = frozenset(
    {
        "cmd",
        "doctype",
        "docname",
        "csrf_token",
        "sid",
        "api_key",
        "api_secret",
    }
)

SUBMIT_FIELDS = frozenset(
    {
        "verification_type",
        "legal_name",
        "phone_number",
        "business_name",
        "business_type",
        "business_category",
        "business_phone_number",
        "business_email",
        "business_website",
        "business_address",
        "verification_documents",
        "idempotency_key",
    }
)

DOCUMENT_FIELDS = frozenset(
    {
        "document_type",
        "document_number",
        "issue_date",
        "expiry_date",
        "media",
        "media_id",
        "attachment_media",
        "attachment_media_id",
    }
)

MAX_DOCUMENTS = 10
MAX_DOCUMENT_TYPE_LENGTH = 140
MAX_DOCUMENT_NUMBER_LENGTH = 160
MAX_LEGAL_NAME_LENGTH = 160
MAX_PHONE_LENGTH = 32
MAX_BUSINESS_NAME_LENGTH = 180
MAX_BUSINESS_CATEGORY_LENGTH = 140
MAX_BUSINESS_EMAIL_LENGTH = 254
MAX_BUSINESS_WEBSITE_LENGTH = 300
MAX_BUSINESS_ADDRESS_LENGTH = 500
MAX_REJECTION_REASON_LENGTH = 1000
MAX_IDEMPOTENCY_KEY_LENGTH = 120

