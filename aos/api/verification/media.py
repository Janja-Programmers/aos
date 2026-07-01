"""Verification media helpers.

Verification documents are sensitive. New submissions must use private
MinIO-backed AOS Media Object records with purpose ``verification_document``.
Frappe File URLs are intentionally not accepted for new verification requests.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
    serialize_media_doc,
)

VERIFICATION_DOCUMENT_PURPOSE = "verification_document"
VERIFICATION_DOCUMENT_FIELD = "verification_documents"


def extract_document_media_id(row: dict[str, Any]) -> str:
    """Return the media id from any supported verification document key."""
    value = (
        row.get("media")
        or row.get("media_id")
        or row.get("attachment_media")
        or row.get("attachment_media_id")
    )

    # Mild migration convenience: if a caller sends the MEDIA-* id in the old
    # attachment key, treat it as the media id. Plain /files URLs remain rejected.
    if not value:
        attachment = str(row.get("attachment") or "").strip()
        if attachment.upper().startswith("MEDIA-"):
            value = attachment

    return str(value or "").strip()


def validate_verification_document_media(*, user: str, media_id: str):
    """Validate one uploaded verification document media object."""
    service = MediaService()
    doc = service.assert_media_ready_for_attach(
        media_id=media_id,
        user=user,
        purpose=VERIFICATION_DOCUMENT_PURPOSE,
    )

    if doc.visibility != "Private":
        raise MediaValidationError("Verification document media must be private")

    return doc


def normalize_verification_documents_for_submit(
    *,
    user: str,
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Validate and normalize verification document payloads for submission.

    Returned rows are safe to append directly to AOS Verification Request's child
    table. The media object is not attached here because the parent request name
    may not exist until after save.
    """
    if not documents:
        frappe.throw("Verification documents are required.")

    if not isinstance(documents, list):
        frappe.throw("Verification documents must be a list.")

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in documents:
        if not isinstance(row, dict):
            frappe.throw("Invalid verification document payload.")

        media_id = extract_document_media_id(row)
        if not media_id:
            if row.get("attachment"):
                frappe.throw(
                    "Verification documents must be uploaded as media_id using purpose verification_document. "
                    "Frappe File URLs are no longer accepted."
                )
            frappe.throw("Each verification document must include media_id.")

        if media_id in seen:
            frappe.throw(f"Duplicate verification document media: {media_id}")
        seen.add(media_id)

        media_doc = validate_verification_document_media(user=user, media_id=media_id)

        normalized.append(
            {
                "document_type": row.get("document_type"),
                "document_number": row.get("document_number"),
                "issue_date": row.get("issue_date"),
                "expiry_date": row.get("expiry_date"),
                "media": media_doc.name,
                # Keep old field populated only with non-sensitive cached value when
                # it is already a URL from legacy rows. New private media should not
                # store signed URLs because they expire.
                "attachment": "",
            }
        )

    return normalized


def attach_verification_document_media(*, user: str, verification_name: str, media_ids: list[str]) -> None:
    """Mark uploaded verification document media as attached to the request."""
    service = MediaService()
    for media_id in media_ids:
        service.attach_media(
            media_id=media_id,
            user=user,
            purpose=VERIFICATION_DOCUMENT_PURPOSE,
            attached_doctype="AOS Verification Request",
            attached_name=verification_name,
            attached_field=VERIFICATION_DOCUMENT_FIELD,
        )


def serialize_verification_document(row, *, user: str | None = None, include_url: bool = False) -> dict[str, Any]:
    """Serialize a verification document row without exposing permanent private URLs."""
    data = {
        "document_type": row.document_type,
        "document_number": row.document_number,
        "issue_date": row.issue_date,
        "expiry_date": row.expiry_date,
        "media": row.media or None,
        "media_id": row.media or None,
        "attachment": row.attachment or None,
    }

    if not row.media:
        return data

    try:
        service = MediaService()
        media_doc = service.get_media_doc(row.media)
        url = service.get_url(media_id=row.media, user=user) if include_url else None
        data["media_object"] = serialize_media_doc(media_doc, url=url)
        if include_url:
            data["url"] = url
    except (MediaNotFoundError, MediaPermissionError, MediaValidationError):
        data["media_object"] = None

    return data
