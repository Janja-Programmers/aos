"""Live-cover integration through the canonical Media service."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.media.consumer_helpers import (
    clean_str,
    media_error_response,
    normalize_media_id,
    public_media_url,
)
from aos.api.shared.responses import fail
from aos.services.media.media_service import MediaService

LIVE_COVER_PURPOSE = "live_cover"
LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_COVER_MEDIA_FIELD = "live_cover_media"


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception):
    return media_error_response(
        exc,
        label="Live cover",
        log_title="AOS Live Cover Media Failed",
    )


def get_live_cover_media_id(live_id: str) -> str:
    if not live_id or not _live_has_cover_media_field():
        return ""
    return clean_str(
        frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            LIVE_COVER_MEDIA_FIELD,
        )
    )


def get_public_media_url(media_id: Any) -> str:
    return public_media_url(media_id)


def validate_live_cover_media_for_use(
    *,
    media_id: Any,
    user: str,
    live_id: str | None = None,
):
    normalized_id = normalize_media_id(media_id)
    if not normalized_id:
        return None, "", fail(
            "Live cover media id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        doc = MediaService().validate_media_for_use(
            media_id=normalized_id,
            user=user,
            purpose=LIVE_COVER_PURPOSE,
            attached_doctype=LIVE_STREAM_DOCTYPE if live_id else None,
            attached_name=live_id,
        )
        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def attach_live_cover_media(*, media_id: Any, user: str, live_id: str):
    normalized_id = normalize_media_id(media_id)
    service = MediaService()
    previous_media_id = get_live_cover_media_id(live_id)

    try:
        doc = service.attach_media(
            media_id=normalized_id,
            user=user,
            purpose=LIVE_COVER_PURPOSE,
            attached_doctype=LIVE_STREAM_DOCTYPE,
            attached_name=live_id,
            attached_field=LIVE_COVER_MEDIA_FIELD,
            replacing_media_id=previous_media_id,
        )

        if previous_media_id and previous_media_id != normalized_id:
            service.release_media(
                media_id=previous_media_id,
                user=user,
                attached_doctype=LIVE_STREAM_DOCTYPE,
                attached_name=live_id,
                replacement_media_id=normalized_id,
            )

        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def _live_has_cover_media_field() -> bool:
    try:
        return bool(
            frappe.get_meta(LIVE_STREAM_DOCTYPE).has_field(LIVE_COVER_MEDIA_FIELD)
        )
    except Exception:
        return False
