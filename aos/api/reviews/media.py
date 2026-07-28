"""Backward-compatible review-image helpers.

The Reviews service is the canonical owner of input normalisation while the
Media service owns content, ownership, lifecycle, storage and URL generation.
This module retains the old helper names as thin delegates for internal callers.
"""

from __future__ import annotations

from typing import Any

from aos.api.media.consumer_helpers import (
    clean_str,
    compatibility_media_error,
    normalize_media_id,
    public_media_url,
)
from aos.api.shared.responses import fail
from aos.services.media.media_service import MediaService
from aos.services.reviews.errors import ReviewValidationError
from aos.services.reviews.validation import normalize_images

REVIEW_IMAGE_PURPOSE = "review_image"
REVIEW_DOCTYPE = "AOS Review"
REVIEW_IMAGE_FIELD = "review_images"


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception, *, index: int | None = None):
    response = compatibility_media_error(
        exc,
        label="Review image",
        log_title="AOS Review Image Media Failed",
    )
    if index is not None:
        response["message"] = f"Review image {index + 1}: {response['message']}"
    return response


def get_public_media_url(media_id: Any) -> str:
    return public_media_url(media_id)


def get_review_image_url(row: Any) -> str:
    media_id = getattr(row, "media", None)
    if isinstance(row, dict):
        media_id = media_id or row.get("media")
    return public_media_url(media_id)


def validate_review_image_media_for_use(
    *,
    media_id: Any,
    user: str,
    index: int | None = None,
):
    normalized_id = normalize_media_id(media_id)
    if not normalized_id:
        return None, "", fail(
            "Review image media id is required.",
            error="VALIDATION_ERROR",
        )
    try:
        doc = MediaService().validate_media_for_use(
            media_id=normalized_id,
            user=user,
            purpose=REVIEW_IMAGE_PURPOSE,
        )
        return doc, public_media_url(doc.name), None
    except Exception as exc:
        return None, "", response_from_media_exception(exc, index=index)


def normalize_review_image_inputs(images: Any):
    """Delegate legacy media-list normalisation to the Reviews policy."""

    try:
        return normalize_images(images), None
    except ReviewValidationError as exc:
        return [], fail(str(exc), error="VALIDATION_ERROR")


def validate_review_images_for_create(*, media_ids: list[str], user: str):
    validated: list[dict[str, Any]] = []
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
    try:
        doc = MediaService().attach_media(
            media_id=normalize_media_id(media_id),
            user=user,
            purpose=REVIEW_IMAGE_PURPOSE,
            attached_doctype=REVIEW_DOCTYPE,
            attached_name=review_id,
            attached_field=REVIEW_IMAGE_FIELD,
        )
        return doc, None
    except Exception as exc:
        return None, response_from_media_exception(exc)


def serialize_review_image(row: Any) -> dict[str, Any]:
    media_id = clean_str(getattr(row, "media", None))
    if isinstance(row, dict):
        media_id = media_id or clean_str(row.get("media"))
    url = public_media_url(media_id)
    return {
        "media": media_id or None,
        "media_id": media_id or None,
        "image": url,
        "url": url,
    }


def _review_image_media_url(doc) -> str:
    return public_media_url(doc.name)
