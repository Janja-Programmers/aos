"""Seller-banner integration through the canonical Media service."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.media.consumer_helpers import (
    clean_str,
    compatibility_media_error,
    normalize_media_id,
    public_media_url,
)
from aos.api.shared.responses import fail
from aos.services.media.media_service import MediaService

SELLER_BANNER_PURPOSE = "seller_banner"
SELLER_DOCTYPE = "AOS Seller"
SELLER_BANNER_MEDIA_FIELD = "shop_banner_media"


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception):
    return compatibility_media_error(
        exc,
        label="Seller banner",
        log_title="AOS Seller Banner Media Failed",
    )


def get_seller_banner_media_id(seller: str) -> str:
    if not seller or not _seller_has_banner_media_field():
        return ""
    return clean_str(
        frappe.db.get_value(
            SELLER_DOCTYPE,
            seller,
            SELLER_BANNER_MEDIA_FIELD,
        )
    )


def get_public_media_url(media_id: Any) -> str:
    return public_media_url(media_id)


def validate_seller_banner_media_for_use(*, media_id: Any, user: str, seller: str):
    normalized_id = normalize_media_id(media_id)
    if not normalized_id:
        return None, "", fail(
            "Seller banner media id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        doc = MediaService().validate_media_for_use(
            media_id=normalized_id,
            user=user,
            purpose=SELLER_BANNER_PURPOSE,
            attached_doctype=SELLER_DOCTYPE,
            attached_name=seller,
        )
        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def attach_seller_banner_media(*, media_id: Any, user: str, seller: str):
    normalized_id = normalize_media_id(media_id)
    service = MediaService()
    previous_media_id = get_seller_banner_media_id(seller)

    try:
        doc = service.attach_media(
            media_id=normalized_id,
            user=user,
            purpose=SELLER_BANNER_PURPOSE,
            attached_doctype=SELLER_DOCTYPE,
            attached_name=seller,
            attached_field=SELLER_BANNER_MEDIA_FIELD,
            replacing_media_id=previous_media_id,
        )

        if previous_media_id and previous_media_id != normalized_id:
            service.release_media(
                media_id=previous_media_id,
                user=user,
                attached_doctype=SELLER_DOCTYPE,
                attached_name=seller,
                replacement_media_id=normalized_id,
            )

        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def clear_seller_banner_media(*, seller: str, user: str) -> None:
    previous_media_id = get_seller_banner_media_id(seller)
    if previous_media_id:
        MediaService().release_media(
            media_id=previous_media_id,
            user=user,
            attached_doctype=SELLER_DOCTYPE,
            attached_name=seller,
        )


def _seller_media_url(doc) -> str:
    return public_media_url(doc.name)


def _seller_has_banner_media_field() -> bool:
    try:
        return bool(
            frappe.get_meta(SELLER_DOCTYPE).has_field(SELLER_BANNER_MEDIA_FIELD)
        )
    except Exception:
        return False


def _release_previous_seller_banner(
    *,
    seller: str,
    owner_user: str,
    new_media_id: str,
) -> None:
    previous_media_id = get_seller_banner_media_id(seller)
    if previous_media_id and previous_media_id != new_media_id:
        MediaService().release_media(
            media_id=previous_media_id,
            user=owner_user,
            attached_doctype=SELLER_DOCTYPE,
            attached_name=seller,
            replacement_media_id=new_media_id,
        )


def _mark_media_orphaned(media_id: str, *, owner_user: str) -> None:
    MediaService().release_media(media_id=media_id, user=owner_user)
