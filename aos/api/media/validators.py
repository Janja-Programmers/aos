from __future__ import annotations

from aos.api.shared.responses import fail
from aos.services.media.media_purposes import list_media_purposes


def require_media_id(value):
    media_id = str(value or "").strip()
    if not media_id:
        return None, fail("Media id is required.", code="VALIDATION_ERROR")
    return media_id, None


def require_upload_init_payload(kwargs: dict):
    purpose = str(kwargs.get("purpose") or "").strip()
    filename = str(kwargs.get("filename") or "").strip()
    content_type = str(kwargs.get("content_type") or "").strip()
    size_bytes = kwargs.get("size_bytes")

    missing = []
    if not purpose:
        missing.append("purpose")
    if not filename:
        missing.append("filename")
    if not content_type:
        missing.append("content_type")
    if size_bytes is None or str(size_bytes).strip() == "":
        missing.append("size_bytes")

    if missing:
        return None, fail(
            "Missing required upload fields.",
            code="VALIDATION_ERROR",
            data={"fields": missing},
        )

    return {
        "purpose": purpose,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
    }, None


def invalid_purpose_response():
    return fail(
        "Invalid media purpose.",
        code="VALIDATION_ERROR",
        data={"allowed_purposes": list_media_purposes()},
    )
