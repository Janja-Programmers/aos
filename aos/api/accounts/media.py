"""Profile image media helpers.

Profile images are public AOS Media Object records. ``User.user_image`` remains
as a compatibility/cache URL for Frappe and existing mobile serializers, while
``AOS Profile.profile_image_media`` stores the media relationship.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.responses import fail
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)

PROFILE_IMAGE_PURPOSE = "profile_image"
PROFILE_DOCTYPE = "AOS Profile"
PROFILE_IMAGE_FIELD = "profile_image_media"


def clean_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_media_id(value: Any) -> str:
    """Normalize a media id supplied as a string or nested object."""
    if isinstance(value, dict):
        value = value.get("media_id") or value.get("id") or value.get("name")
    return clean_str(value)


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception):
    message = str(exc) or "Invalid profile image media."

    if isinstance(exc, MediaNotFoundError):
        return fail("Profile image media not found.", code="NOT_FOUND")

    if isinstance(exc, MediaPermissionError):
        return fail(message, code="FORBIDDEN")

    if isinstance(exc, MediaValidationError):
        return fail(message, code="VALIDATION_ERROR")

    frappe.log_error(frappe.get_traceback(), "AOS Profile Image Media Failed")
    return fail("Failed to validate profile image media.", code="INTERNAL_ERROR")


def get_profile_image_media_id(user: str) -> str:
    if not user:
        return ""

    if not _profile_has_media_field():
        return ""

    return clean_str(
        frappe.db.get_value(
            PROFILE_DOCTYPE,
            user,
            PROFILE_IMAGE_FIELD,
        )
    )


def get_public_media_url(media_id: Any) -> str:
    media_id = normalize_media_id(media_id)
    if not media_id:
        return ""

    row = frappe.db.get_value(
        "AOS Media Object",
        media_id,
        ["name", "status", "visibility", "public_url", "bucket", "object_key"],
        as_dict=True,
    )

    if not row or row.status == "Deleted" or row.visibility != "Public":
        return ""

    if clean_str(row.public_url):
        return clean_str(row.public_url)

    try:
        return MediaService().get_url(media_id=media_id, user=None)
    except Exception:
        return ""


def validate_profile_image_media_for_use(*, media_id: Any, user: str):
    """Validate that a profile image media object can be used by this user.

    New media must be Uploaded/unattached. Already attached media is allowed only
    when it is already attached to the same user's AOS Profile.
    """
    media_id = normalize_media_id(media_id)

    if not media_id:
        return None, "", fail("Profile image media id is required.", code="VALIDATION_ERROR")

    service = MediaService()

    try:
        doc = service.get_media_doc(media_id)
        service.assert_user_can_manage(doc, user)

        if doc.status == "Deleted":
            return None, "", fail("Profile image media not found.", code="NOT_FOUND")

        if doc.purpose != PROFILE_IMAGE_PURPOSE:
            return None, "", fail(
                "Profile image media has the wrong purpose.",
                code="VALIDATION_ERROR",
            )

        if doc.visibility != "Public":
            return None, "", fail(
                "Profile image media must be public.",
                code="VALIDATION_ERROR",
            )

        if doc.status == "Uploaded":
            return doc, _profile_media_url(doc), None

        if doc.status == "Attached":
            if doc.attached_doctype == PROFILE_DOCTYPE and doc.attached_name == user:
                return doc, _profile_media_url(doc), None

        return None, "", fail(
            "Profile image media cannot be used in its current state.",
            code="VALIDATION_ERROR",
        )

    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def attach_profile_image_media(*, media_id: Any, user: str):
    """Attach profile image media to the user's AOS Profile and return URL."""
    media_id = normalize_media_id(media_id)
    doc, url, err = validate_profile_image_media_for_use(media_id=media_id, user=user)
    if err:
        return None, "", err

    _release_previous_profile_image(user=user, new_media_id=media_id)

    profile_doc = frappe.get_doc(PROFILE_DOCTYPE, user)
    if _profile_has_media_field():
        profile_doc.set(PROFILE_IMAGE_FIELD, media_id)
        profile_doc.save(ignore_permissions=True)

    if doc.status != "Attached":
        doc.status = "Attached"
        doc.attached_doctype = PROFILE_DOCTYPE
        doc.attached_name = user
        doc.attached_field = PROFILE_IMAGE_FIELD
        doc.attached_at = now_datetime()
        doc.save(ignore_permissions=True)

    return doc, url, None


def clear_profile_image_media(*, user: str) -> None:
    """Clear the profile media link and retire the previously linked media."""
    old_media_id = get_profile_image_media_id(user)
    if old_media_id:
        _mark_media_orphaned(old_media_id, owner_user=user)

    if _profile_has_media_field() and frappe.db.exists(PROFILE_DOCTYPE, user):
        profile_doc = frappe.get_doc(PROFILE_DOCTYPE, user)
        profile_doc.set(PROFILE_IMAGE_FIELD, "")
        profile_doc.save(ignore_permissions=True)


def _profile_media_url(doc) -> str:
    if clean_str(getattr(doc, "public_url", None)):
        return clean_str(doc.public_url)
    return MediaService().get_url(media_id=doc.name, user=None)


def _profile_has_media_field() -> bool:
    try:
        return bool(frappe.get_meta(PROFILE_DOCTYPE).has_field(PROFILE_IMAGE_FIELD))
    except Exception:
        return False


def _release_previous_profile_image(*, user: str, new_media_id: str) -> None:
    old_media_id = get_profile_image_media_id(user)
    if old_media_id and old_media_id != new_media_id:
        _mark_media_orphaned(old_media_id, owner_user=user)


def _mark_media_orphaned(media_id: str, *, owner_user: str) -> None:
    """Detach a replaced profile image so cleanup can remove it later.

    This is best-effort. Public URL caches on old historical payloads are not
    treated as source of truth.
    """
    try:
        doc = MediaService().get_media_doc(media_id)
        if doc.owner_user != owner_user:
            return
        if doc.status not in {"Deleted", "Delete Pending"}:
            doc.status = "Delete Pending"
            doc.attached_doctype = ""
            doc.attached_name = ""
            doc.attached_field = ""
            doc.save(ignore_permissions=True)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Profile Image Media Release Failed",
        )
