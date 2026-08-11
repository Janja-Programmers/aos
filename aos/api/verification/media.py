"""Legacy Verification media compatibility helpers.

Verification documents are sensitive and must use private MinIO-backed
``AOS Media Object`` records with purpose ``verification_document``.  New code
should use :mod:`aos.services.verification`; these helpers remain intentionally
small so older imports cannot bypass the hardened Verification boundary.
"""

from __future__ import annotations

from typing import Any

from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)
from aos.services.verification.errors import VerificationValidationError
from aos.services.verification.serializers import serialize_document
from aos.services.verification.validation import normalize_documents

VERIFICATION_DOCUMENT_PURPOSE = "verification_document"
VERIFICATION_DOCUMENT_FIELD = "verification_documents"


def extract_document_media_id(row: dict[str, Any]) -> str:
    """Return the media id from supported legacy document keys."""
    value = (
        row.get("media")
        or row.get("media_id")
        or row.get("attachment_media")
        or row.get("attachment_media_id")
    )
    return str(value or "").strip()


def validate_verification_document_media(*, user: str, media_id: str):
    """Validate one private uploaded Verification media object for ``user``."""
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
    """Strictly normalize legacy document payloads and verify media ownership.

    The parent request may not exist yet, so this does not attach the media.
    """
    try:
        rows = normalize_documents(documents)
    except VerificationValidationError:
        raise

    normalized: list[dict[str, Any]] = []
    for row in rows:
        try:
            media_doc = validate_verification_document_media(user=user, media_id=row["media_id"])
        except (MediaNotFoundError, MediaPermissionError, MediaValidationError) as exc:
            # Avoid exposing existence/ownership of arbitrary private media IDs.
            raise VerificationValidationError(
                "Invalid verification document media.", code="VERIFICATION_INVALID_DOCUMENT"
            ) from exc
        normalized.append(
            {
                "document_type": row["document_type"],
                "document_number": row["document_number"],
                "issue_date": row["issue_date"],
                "expiry_date": row["expiry_date"],
                "media": media_doc.name,
                "attachment": "",
            }
        )
    return normalized


def attach_verification_document_media(*, user: str, verification_name: str, media_ids: list[str]) -> None:
    """Attach already validated private media to an existing Verification request."""
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
    """Serialize a document with masking and optional authorized short-lived URL.

    This compatibility helper deliberately does not return raw Media Object
    metadata (storage keys, checksums, original filenames, or permanent URLs).
    """
    data = serialize_document(row)
    if not getattr(row, "media", None) or not include_url:
        return data

    try:
        data["url"] = MediaService().get_url(media_id=row.media, user=user)
    except (MediaNotFoundError, MediaPermissionError, MediaValidationError):
        data["url"] = None
    return data
