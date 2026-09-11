"""Verification evidence policy built on the canonical Media service."""

from __future__ import annotations

from aos.services.media.media_service import MediaError, MediaService

from .constants import VERIFICATION_DOCUMENT_PURPOSE, VERIFICATION_DOCTYPE
from .errors import VerificationValidationError


def validate_evidence_media(
    *,
    media: MediaService,
    media_id: str,
    user: str,
    verification_name: str | None = None,
):
    """Validate one private verification evidence object without leaking IDOR detail."""
    try:
        doc = media.validate_media_for_use(
            media_id=media_id,
            user=user,
            purpose=VERIFICATION_DOCUMENT_PURPOSE,
            attached_doctype=VERIFICATION_DOCTYPE if verification_name else None,
            attached_name=verification_name,
        )
    except MediaError as exc:
        raise VerificationValidationError(
            "Invalid verification document media.", code="VERIFICATION_INVALID_DOCUMENT"
        ) from exc
    if str(getattr(doc, "visibility", "")) != "Private":
        raise VerificationValidationError(
            "Invalid verification document media.", code="VERIFICATION_INVALID_DOCUMENT"
        )
    return doc
