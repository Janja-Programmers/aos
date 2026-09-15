"""Ads-to-Media orchestration using the canonical Media service."""

from __future__ import annotations

from typing import Any, Iterable

from aos.services.media.media_service import MediaService

from .errors import AdsValidationError

AD_DOCTYPE = "AOS Ad"


def prepare_image_rows(rows: Iterable[dict[str, Any]], *, user: str, ad_name: str | None = None) -> list[dict[str, Any]]:
    service = MediaService()
    prepared: list[dict[str, Any]] = []
    for row in rows:
        media_id = str(row.get("media") or "").strip()
        if not media_id:
            raise AdsValidationError("Image media id is required.", code="AD_MEDIA_REQUIRED")
        media = service.validate_media_for_use(
            media_id=media_id,
            user=user,
            purpose="ad_image",
            attached_doctype=AD_DOCTYPE if ad_name else None,
            attached_name=ad_name,
        )
        prepared.append(
            {
                "media": media.name,
                "is_primary": int(row.get("is_primary") or 0),
                "sort_order": int(row.get("sort_order") or 0),
            }
        )
    return prepared


def prepare_video(media_id: Any, *, user: str, ad_name: str | None = None) -> str | None:
    clean_id = str(media_id or "").strip()
    if not clean_id:
        return None
    media = MediaService().validate_media_for_use(
        media_id=clean_id,
        user=user,
        purpose="ad_video",
        attached_doctype=AD_DOCTYPE if ad_name else None,
        attached_name=ad_name,
    )
    return media.name


def attach_all(*, user: str, ad_name: str, image_ids: Iterable[str], video_id: str | None) -> None:
    service = MediaService()
    for media_id in image_ids:
        service.attach_media(
            media_id=media_id,
            user=user,
            purpose="ad_image",
            attached_doctype=AD_DOCTYPE,
            attached_name=ad_name,
            attached_field="images",
        )
    if video_id:
        service.attach_media(
            media_id=video_id,
            user=user,
            purpose="ad_video",
            attached_doctype=AD_DOCTYPE,
            attached_name=ad_name,
            attached_field="video_media",
        )


def release_removed(
    *,
    user: str,
    ad_name: str,
    previous_ids: Iterable[str],
    current_ids: Iterable[str],
) -> None:
    service = MediaService()
    current = {str(value).strip() for value in current_ids if str(value or "").strip()}
    for media_id in {str(value).strip() for value in previous_ids if str(value or "").strip()} - current:
        service.release_media(
            media_id=media_id,
            user=user,
            attached_doctype=AD_DOCTYPE,
            attached_name=ad_name,
        )


def release_all(*, user: str, ad_name: str, media_ids: Iterable[str]) -> None:
    release_removed(user=user, ad_name=ad_name, previous_ids=media_ids, current_ids=())
