"""Backward-compatible avatar facade using the Accounts and Media services."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.media.consumer_helpers import compatibility_media_error, normalize_media_id, public_media_url
from aos.services.accounts.profile_service import AccountProfileService
from aos.services.media.media_service import MediaService

PROFILE_IMAGE_PURPOSE = "profile_image"
PROFILE_DOCTYPE = "AOS Profile"
PROFILE_IMAGE_FIELD = "profile_image_media"


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception):
    return compatibility_media_error(exc, label="Profile image", log_title="AOS Profile Image Media Failed")


def get_profile_image_media_id(user: str) -> str:
    return str(frappe.db.get_value(PROFILE_DOCTYPE, user, PROFILE_IMAGE_FIELD) or "")


def get_public_media_url(media_id: Any) -> str:
    return public_media_url(media_id)


def validate_profile_image_media_for_use(*, media_id: Any, user: str):
    try:
        doc = MediaService().validate_media_for_use(
            media_id=normalize_media_id(media_id),
            user=user,
            purpose=PROFILE_IMAGE_PURPOSE,
            attached_doctype=PROFILE_DOCTYPE,
            attached_name=user,
        )
        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def attach_profile_image_media(*, media_id: Any, user: str):
    try:
        data = AccountProfileService().update_profile(user=user, payload={"avatar_media_id": media_id})
        media_doc = MediaService().get_media_doc(normalize_media_id(media_id))
        return media_doc, data.get("avatar") or data.get("user_image") or "", None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def clear_profile_image_media(*, user: str) -> None:
    AccountProfileService().remove_avatar(user=user)
