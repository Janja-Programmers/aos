from __future__ import annotations

from aos.api.shared.responses import fail
from aos.services.media.identifiers import normalize_media_id


def reject_unknown_fields(kwargs: dict, *, allowed: set[str]):
    unknown = sorted(set(kwargs) - allowed)
    if unknown:
        return fail(
            "Unsupported media request fields.",
            error="VALIDATION_ERROR",
            data={"fields": unknown},
        )
    return None


def require_media_id(value):
    if value in (None, ""):
        return None, fail("Media id is required.", error="VALIDATION_ERROR")
    media_id = normalize_media_id(value)
    if media_id is None:
        return None, fail("Invalid media id.", error="VALIDATION_ERROR")
    return media_id, None


def require_upload_init_payload(kwargs: dict):
    purpose = str(kwargs.get("purpose") or "").strip()
    filename = str(kwargs.get("filename") or "").strip()
    content_type = str(kwargs.get("content_type") or "").strip()
    size_bytes = kwargs.get("size_bytes")
    missing = [name for name, value in (
        ("purpose", purpose), ("filename", filename), ("content_type", content_type),
    ) if not value]
    if size_bytes is None or str(size_bytes).strip() == "":
        missing.append("size_bytes")
    if missing:
        return None, fail("Missing required upload fields.", error="VALIDATION_ERROR", data={"fields": missing})
    payload = {
        "purpose": purpose,
        "filename": filename,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "duration_seconds": kwargs.get("duration_seconds"),
        "checksum_sha256": kwargs.get("checksum_sha256"),
        "idempotency_key": kwargs.get("idempotency_key"),
    }

    upload_mode = kwargs.get("upload_mode")
    if upload_mode is not None and str(upload_mode).strip():
        payload["upload_mode"] = upload_mode

    return payload, None
