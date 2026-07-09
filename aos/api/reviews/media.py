"""Review image media helpers.

Review images are public AOS Media Object records. ``AOS Review Image.media``
is the source of truth; ``image`` is only a generated public URL cache.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import frappe
from frappe.utils import now_datetime

from aos.api.shared.responses import fail
from aos.api.shared.public_errors import safe_exception_message
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)

REVIEW_IMAGE_PURPOSE = "review_image"
REVIEW_DOCTYPE = "AOS Review"
REVIEW_IMAGE_FIELD = "review_images"


def clean_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_media_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("media_id") or value.get("media") or value.get("id") or value.get("name")
    return clean_str(value)


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception, *, index: int | None = None):
    prefix = f"Review image {index + 1}: " if index is not None else ""

    if isinstance(exc, MediaNotFoundError):
        return fail(f"{prefix}media not found.", error="NOT_FOUND")

    if isinstance(exc, MediaPermissionError):
        return fail(prefix + safe_exception_message(exc, "Not allowed."), error="FORBIDDEN")

    if isinstance(exc, MediaValidationError):
        return fail(prefix + safe_exception_message(exc, "Invalid review image media."), error="VALIDATION_ERROR")

    frappe.log_error(frappe.get_traceback(), "AOS Review Image Media Failed")
    return fail(f"{prefix}failed to validate media.", error="INTERNAL_ERROR")


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


def get_review_image_url(row: Any) -> str:
    media_id = clean_str(
        getattr(row, "media", None)
        or (row.get("media") if isinstance(row, dict) else None)
    )
    return get_public_media_url(media_id)


def validate_review_image_media_for_use(*, media_id: Any, user: str, index: int | None = None):
    media_id = normalize_media_id(media_id)

    if not media_id:
        return None, "", fail("Review image media id is required.", error="VALIDATION_ERROR")

    service = MediaService()

    try:
        doc = service.get_media_doc(media_id)
        service.assert_user_can_manage(doc, user)

        if doc.status == "Deleted":
            return None, "", fail("Review image media not found.", error="NOT_FOUND")

        if doc.purpose != REVIEW_IMAGE_PURPOSE:
            return None, "", fail(
                "Review image media has the wrong purpose.",
                error="VALIDATION_ERROR",
            )

        if doc.visibility != "Public":
            return None, "", fail(
                "Review image media must be public.",
                error="VALIDATION_ERROR",
            )

        if doc.status != "Uploaded":
            return None, "", fail(
                "Review image media cannot be used in its current state.",
                error="VALIDATION_ERROR",
            )

        return doc, _review_image_media_url(doc), None

    except Exception as exc:
        return None, "", response_from_media_exception(exc, index=index)


def normalize_review_image_inputs(images: Any) -> Tuple[List[str], Any | None]:
    """Normalize create-review image inputs into media ids.

    New reviews must submit media ids. URL strings are rejected.
    """
    if images in (None, ""):
        return [], None

    if not isinstance(images, list):
        return [], fail("Images must be a list.", error="VALIDATION_ERROR")

    media_ids: List[str] = []
    seen: set[str] = set()

    for index, item in enumerate(images):
        media_id = normalize_media_id(item)

        if not media_id:
            continue

        if not looks_like_media_id(media_id):
            return [], fail(
                f"Review image {index + 1} must be uploaded media_id. Upload with purpose=review_image first.",
                error="VALIDATION_ERROR",
            )

        if media_id in seen:
            return [], fail(
                f"Review image {index + 1} is duplicated.",
                error="VALIDATION_ERROR",
            )

        seen.add(media_id)
        media_ids.append(media_id)

    if len(media_ids) > 5:
        return [], fail("Maximum 5 images allowed.", error="VALIDATION_ERROR")

    return media_ids, None


def validate_review_images_for_create(*, media_ids: List[str], user: str):
    validated: List[Dict[str, Any]] = []

    for index, media_id in enumerate(media_ids):
        doc, url, err = validate_review_image_media_for_use(
            media_id=media_id,
            user=user,
            index=index,
        )
        if err:
            return [], err
        validated.append({"media": doc.name, "url": url})

    return validated, None


def attach_review_image_media(*, media_id: Any, user: str, review_id: str):
    media_id = normalize_media_id(media_id)
    service = MediaService()

    try:
        doc = service.get_media_doc(media_id)
        service.assert_user_can_manage(doc, user)

        if doc.status != "Uploaded":
            return None, fail(
                "Review image media must be uploaded before it can be attached.",
                error="VALIDATION_ERROR",
            )

        if doc.purpose != REVIEW_IMAGE_PURPOSE:
            return None, fail("Review image media has the wrong purpose.", error="VALIDATION_ERROR")

        doc.status = "Attached"
        doc.attached_doctype = REVIEW_DOCTYPE
        doc.attached_name = review_id
        doc.attached_field = REVIEW_IMAGE_FIELD
        doc.attached_at = now_datetime()
        doc.save(ignore_permissions=True)
        return doc, None

    except Exception as exc:
        return None, response_from_media_exception(exc)


def serialize_review_image(row: Any) -> Dict[str, Any]:
    media_id = clean_str(
        getattr(row, "media", None)
        or (row.get("media") if isinstance(row, dict) else None)
    )
    url = get_public_media_url(media_id) if media_id else ""

    return {
        "media": media_id or None,
        "media_id": media_id or None,
        "image": url,
        "url": url,
    }


def _review_image_media_url(doc) -> str:
    if clean_str(getattr(doc, "public_url", None)):
        return clean_str(doc.public_url)
    return MediaService().get_url(media_id=doc.name, user=None)
