"""Ad image/video integration through the canonical Media service."""

from __future__ import annotations

from typing import Any

from aos.api.media.consumer_helpers import (
    media_error_response,
    normalize_media_id,
    public_media_url,
)
from aos.api.shared.responses import fail
from aos.services.media.media_service import MediaService

AD_DOCTYPE = "AOS Ad"


def response_from_media_exception(exc: Exception, *, kind: str):
    return media_error_response(
        exc,
        label=kind,
        log_title=f"AOS Ads {kind} Media Failed",
    )


def get_media_public_url(media_id: Any) -> str:
    return public_media_url(media_id)


def get_ad_image_url(row: Any) -> str:
    media_id = getattr(row, "media", None)
    if isinstance(row, dict):
        media_id = media_id or row.get("media")
    return public_media_url(media_id)


def get_ad_video_url(ad_doc: Any) -> str:
    media_id = getattr(ad_doc, "video_media", None)
    if isinstance(ad_doc, dict):
        media_id = media_id or ad_doc.get("video_media")
    return public_media_url(media_id)


def validate_ad_media_for_use(
    *,
    media_id: Any,
    user: str,
    purpose: str,
    kind: str,
    ad_name: str | None = None,
):
    normalized_id = normalize_media_id(media_id)
    if not normalized_id:
        return None, fail(
            f"{kind} media id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        doc = MediaService().validate_media_for_use(
            media_id=normalized_id,
            user=user,
            purpose=purpose,
            attached_doctype=AD_DOCTYPE if ad_name else None,
            attached_name=ad_name,
        )
        return doc, None
    except Exception as exc:
        return None, response_from_media_exception(exc, kind=kind)


def attach_ad_media(
    *,
    media_id: Any,
    user: str,
    purpose: str,
    ad_name: str,
    attached_field: str,
):
    normalized_id = normalize_media_id(media_id)
    if not normalized_id:
        return None, None

    try:
        doc = MediaService().attach_media(
            media_id=normalized_id,
            user=user,
            purpose=purpose,
            attached_doctype=AD_DOCTYPE,
            attached_name=ad_name,
            attached_field=attached_field,
        )
        return doc, None
    except Exception as exc:
        return None, response_from_media_exception(exc, kind="Ad")
