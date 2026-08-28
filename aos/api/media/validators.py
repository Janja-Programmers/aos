from __future__ import annotations

import re

from aos.api.shared.responses import fail
from aos.services.media.media_purposes import list_media_purposes

_MEDIA_ID_RE = re.compile(r"^MEDIA-[A-Za-z0-9._-]{1,96}$")


def require_media_id(value):
    media_id = str(value or "").strip()
    if not media_id:
        return None, fail("Media id is required.", error="VALIDATION_ERROR")
    if len(media_id) > 128 or "\x00" in media_id or not _MEDIA_ID_RE.fullmatch(media_id):
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
        "checksum_sha256": kwargs.get("checksum_sha256") or kwargs.get("checksum"),
        "idempotency_key": kwargs.get("idempotency_key"),
    }

    # Preserve the pre-multipart service-call contract for existing/direct
    # upload clients. ``MediaService.init_upload`` already defaults
    # ``upload_mode`` to None, so forwarding an explicit ``None`` adds no
    # behavior but does break strict mocks/adapters built against the stable
    # call shape. Only forward the new option when the caller actually asks
    # for a mode (for example ``auto`` or ``multipart``).
    upload_mode = kwargs.get("upload_mode") or kwargs.get("mode")
    if upload_mode is not None and str(upload_mode).strip():
        payload["upload_mode"] = upload_mode

    return payload, None


def invalid_purpose_response():
    return fail(
        "Invalid media purpose.",
        error="INVALID_MEDIA_PURPOSE",
        data={"allowed_purposes": list_media_purposes(client_upload_only=True)},
    )
