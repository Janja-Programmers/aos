"""Profile-image integration through the canonical Media service."""

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

PROFILE_IMAGE_PURPOSE = "profile_image"
PROFILE_DOCTYPE = "AOS Profile"
PROFILE_IMAGE_FIELD = "profile_image_media"


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception):
    return compatibility_media_error(
        exc,
        label="Profile image",
        log_title="AOS Profile Image Media Failed",
    )


def get_profile_image_media_id(user: str) -> str:
    if not user or not _profile_has_media_field():
        return ""
    return clean_str(frappe.db.get_value(PROFILE_DOCTYPE, user, PROFILE_IMAGE_FIELD))


def get_public_media_url(media_id: Any) -> str:
    return public_media_url(media_id)


def validate_profile_image_media_for_use(*, media_id: Any, user: str):
    normalized_id = normalize_media_id(media_id)
    if not normalized_id:
        return None, "", fail(
            "Profile image media id is required.",
            error="VALIDATION_ERROR",
        )

    try:
        doc = MediaService().validate_media_for_use(
            media_id=normalized_id,
            user=user,
            purpose=PROFILE_IMAGE_PURPOSE,
            attached_doctype=PROFILE_DOCTYPE,
            attached_name=user,
        )
        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def attach_profile_image_media(*, media_id: Any, user: str):
    normalized_id = normalize_media_id(media_id)
    service = MediaService()
    previous_media_id = get_profile_image_media_id(user)

    try:
        doc = service.attach_media(
            media_id=normalized_id,
            user=user,
            purpose=PROFILE_IMAGE_PURPOSE,
            attached_doctype=PROFILE_DOCTYPE,
            attached_name=user,
            attached_field=PROFILE_IMAGE_FIELD,
            replacing_media_id=previous_media_id,
        )

        if _profile_has_media_field():
            profile = frappe.get_doc(PROFILE_DOCTYPE, user)
            profile.set(PROFILE_IMAGE_FIELD, normalized_id)
            profile.save(ignore_permissions=True)

        if previous_media_id and previous_media_id != normalized_id:
            service.release_media(
                media_id=previous_media_id,
                user=user,
                attached_doctype=PROFILE_DOCTYPE,
                attached_name=user,
                replacement_media_id=normalized_id,
            )

        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def clear_profile_image_media(*, user: str) -> None:
    previous_media_id = get_profile_image_media_id(user)

    if _profile_has_media_field() and frappe.db.exists(PROFILE_DOCTYPE, user):
        profile = frappe.get_doc(PROFILE_DOCTYPE, user)
        profile.set(PROFILE_IMAGE_FIELD, "")
        profile.save(ignore_permissions=True)

    if previous_media_id:
        MediaService().release_media(
            media_id=previous_media_id,
            user=user,
            attached_doctype=PROFILE_DOCTYPE,
            attached_name=user,
        )


def _profile_media_url(doc) -> str:
    return public_media_url(doc.name)


def _profile_has_media_field() -> bool:
    try:
        return bool(frappe.get_meta(PROFILE_DOCTYPE).has_field(PROFILE_IMAGE_FIELD))
    except Exception:
        return False


def _release_previous_profile_image(*, user: str, new_media_id: str) -> None:
    previous_media_id = get_profile_image_media_id(user)
    if previous_media_id and previous_media_id != new_media_id:
        MediaService().release_media(
            media_id=previous_media_id,
            user=user,
            attached_doctype=PROFILE_DOCTYPE,
            attached_name=user,
            replacement_media_id=new_media_id,
        )


def _mark_media_orphaned(media_id: str, *, owner_user: str) -> None:
    MediaService().release_media(media_id=media_id, user=owner_user)
