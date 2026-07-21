"""Canonical public error mapping for the Media API boundary."""

from __future__ import annotations

import frappe

from aos.api.shared.responses import fail
from aos.services.media.media_service import MediaError

_PUBLIC_MESSAGES = {
    "AUTH_REQUIRED": "Authentication is required.",
    "INVALID_MEDIA_PURPOSE": "Invalid media purpose.",
    "UNSUPPORTED_MEDIA_TYPE": "Unsupported media type.",
    "FILE_TOO_LARGE": "File exceeds the allowed size.",
    "INVALID_FILE": "Invalid media file.",
    "INVALID_FILENAME": "Invalid filename.",
    "INVALID_CHECKSUM": "Invalid file checksum.",
    "MEDIA_NOT_FOUND": "Media not found.",
    "MEDIA_NOT_READY": "Media is not ready.",
    "MEDIA_ALREADY_ATTACHED": "Media is already attached.",
    "MEDIA_OWNERSHIP_REQUIRED": "Media ownership is required.",
    "MEDIA_ACCESS_DENIED": "Media access denied.",
    "UPLOAD_EXPIRED": "Upload has expired.",
    "UPLOAD_INCOMPLETE": "Upload is incomplete.",
    "SIZE_MISMATCH": "Uploaded file size does not match.",
    "CHECKSUM_MISMATCH": "Uploaded file checksum does not match.",
    "STORAGE_UNAVAILABLE": "Media storage is temporarily unavailable.",
    "PROCESSING_FAILED": "Media processing failed.",
    "RESOURCE_NOT_FOUND": "Owning resource not found.",
    "MEDIA_LIMIT_EXCEEDED": "Media limit for this resource has been reached.",
    "INVALID_STATE": "Media cannot be used in its current state.",
    "VALIDATION_ERROR": "Invalid request.",
}


def media_error_response(exc: Exception, *, log_title: str | None = None):
    """Return a stable, sanitized Media API failure response."""
    code = str(getattr(exc, "code", "INTERNAL_ERROR") or "INTERNAL_ERROR").upper()
    if not isinstance(exc, MediaError) and code == "INTERNAL_ERROR":
        if log_title:
            frappe.log_error(frappe.get_traceback(), log_title)
        return fail("Media request failed.", error="INTERNAL_ERROR")
    message = _PUBLIC_MESSAGES.get(code, "Media request failed.")
    return fail(message, error=code)
