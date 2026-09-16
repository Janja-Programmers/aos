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
    if isinstance(row, dict):
        projected = str(row.get("url") or "").strip()
        if projected:
            return projected
        media_id = row.get("media")
    else:
        projected = str(getattr(row, "url", "") or "").strip()
        if projected:
            return projected
        media_id = getattr(row, "media", None)
    return public_media_url(media_id)


def get_ad_video_url(ad_doc: Any) -> str:
    if isinstance(ad_doc, dict):
        projected = str(ad_doc.get("video_url") or "").strip()
        if projected:
            return projected
        media_id = ad_doc.get("video_media")
    else:
        projected = str(getattr(ad_doc, "video_url", "") or "").strip()
        if projected:
            return projected
        media_id = getattr(ad_doc, "video_media", None)
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


def project_ad_image_urls(rows: list[Any]) -> list[Any]:
    """Attach canonical public URLs to bounded AOS Ad Image rows in one query."""
    attachments: list[tuple[str, str]] = []
    for row in rows or []:
        if isinstance(row, dict):
            media_id = str(row.get("media") or "").strip()
            parent = str(row.get("parent") or "").strip()
        else:
            media_id = str(getattr(row, "media", "") or "").strip()
            parent = str(getattr(row, "parent", "") or "").strip()
        if media_id and parent:
            attachments.append((media_id, parent))
    urls = (
        MediaService().get_public_attachment_url_map(
            attachments,
            purpose="ad_image",
            attached_doctype=AD_DOCTYPE,
            attached_field="images",
        )
        if attachments else {}
    )
    for row in rows or []:
        if isinstance(row, dict):
            key=(str(row.get("media") or "").strip(), str(row.get("parent") or "").strip())
            row["url"] = urls.get(key, "")
        else:
            key=(str(getattr(row,"media","") or "").strip(), str(getattr(row,"parent","") or "").strip())
            setattr(row, "url", urls.get(key, ""))
    return rows


def project_ad_thumbnail_urls(
    rows: list[Any],
    *,
    ad_field: str = "ad",
    media_field: str = "ad_thumbnail_media",
    output_field: str = "ad_thumbnail",
) -> list[Any]:
    """Project one canonical Ad image Media reference per response row in one query."""
    attachments: list[tuple[str, str]] = []
    for row in rows or []:
        if isinstance(row, dict):
            media_id = str(row.get(media_field) or "").strip()
            ad_name = str(row.get(ad_field) or "").strip()
        else:
            media_id = str(getattr(row, media_field, "") or "").strip()
            ad_name = str(getattr(row, ad_field, "") or "").strip()
        if media_id and ad_name:
            attachments.append((media_id, ad_name))

    urls = (
        MediaService().get_public_attachment_url_map(
            attachments,
            purpose="ad_image",
            attached_doctype=AD_DOCTYPE,
            attached_field="images",
        )
        if attachments
        else {}
    )

    for row in rows or []:
        if isinstance(row, dict):
            media_id = str(row.get(media_field) or "").strip()
            ad_name = str(row.get(ad_field) or "").strip()
            row[output_field] = urls.get((media_id, ad_name), "")
        else:
            media_id = str(getattr(row, media_field, "") or "").strip()
            ad_name = str(getattr(row, ad_field, "") or "").strip()
            setattr(row, output_field, urls.get((media_id, ad_name), ""))
    return rows


def project_ad_video_url(ad_doc: Any) -> str:
    """Project the one canonical attached Ad video through Media."""
    media_id = str(getattr(ad_doc, "video_media", "") or "").strip()
    ad_name = str(getattr(ad_doc, "name", "") or "").strip()
    if not media_id or not ad_name:
        setattr(ad_doc, "video_url", "")
        return ""
    urls = MediaService().get_public_attachment_url_map(
        [(media_id, ad_name)],
        purpose="ad_video",
        attached_doctype=AD_DOCTYPE,
        attached_field="video_media",
    )
    url=urls.get((media_id,ad_name), "")
    setattr(ad_doc, "video_url", url)
    return url
