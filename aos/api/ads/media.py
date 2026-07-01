"""Ads media helpers for MinIO-backed AOS Media Object usage.

Ads now treat `media_id` as the source of truth for images/videos. The legacy
`image`/`video` URL fields are kept as cached response-friendly URLs while the
actual storage ownership lives in AOS Media Object.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

import frappe
from frappe.utils import now_datetime

from aos.api.shared.responses import fail
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
)


def clean_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_media_id(value: Any) -> str:
    """Normalize a media id value supplied by mobile clients.

    Supports either a plain string (`MEDIA-...`) or a nested media object with
    `id`, `media_id`, or `name`.
    """

    if isinstance(value, dict):
        value = value.get("media_id") or value.get("id") or value.get("name")
    return clean_str(value)


def response_from_media_exception(exc: Exception, *, kind: str):
    message = str(exc) or f"Invalid {kind.lower()} media."

    if isinstance(exc, MediaNotFoundError):
        return fail(f"{kind} media not found.", code="NOT_FOUND")

    if isinstance(exc, MediaPermissionError):
        return fail(message, code="FORBIDDEN")

    if isinstance(exc, MediaValidationError):
        return fail(message, code="VALIDATION_ERROR")

    frappe.log_error(frappe.get_traceback(), f"AOS Ads {kind} Media Failed")
    return fail(f"Failed to validate {kind.lower()} media.", code="INTERNAL_ERROR")


def get_media_public_url(media_id: Any) -> str:
    media_id = normalize_media_id(media_id)
    if not media_id:
        return ""

    row = frappe.db.get_value(
        "AOS Media Object",
        media_id,
        ["name", "status", "visibility", "public_url", "bucket", "object_key"],
        as_dict=True,
    )

    if not row or row.status == "Deleted":
        return ""

    if row.visibility != "Public":
        return ""

    if clean_str(row.public_url):
        return clean_str(row.public_url)

    try:
        return MediaService().get_url(media_id=media_id, user=None)
    except Exception:
        return ""


def get_ad_image_url(row: Any) -> str:
    media_id = clean_str(getattr(row, "media", None) or (row.get("media") if isinstance(row, dict) else None))
    fallback = clean_str(getattr(row, "image", None) or (row.get("image") if isinstance(row, dict) else None))
    return get_media_public_url(media_id) or fallback


def get_ad_video_url(ad_doc: Any) -> str:
    media_id = clean_str(getattr(ad_doc, "video_media", None) or (ad_doc.get("video_media") if isinstance(ad_doc, dict) else None))
    fallback = clean_str(getattr(ad_doc, "video", None) or (ad_doc.get("video") if isinstance(ad_doc, dict) else None))
    return get_media_public_url(media_id) or fallback


def validate_ad_media_for_use(
    *,
    media_id: Any,
    user: str,
    purpose: str,
    kind: str,
    ad_name: str | None = None,
) -> Tuple[object | None, Any | None]:
    """Validate an uploaded media object can be used by an ad.

    For create, media must be Uploaded and unattached. For edit, already-attached
    media is allowed only when it is attached to the same ad.
    """

    media_id = normalize_media_id(media_id)

    if not media_id:
        return None, fail(f"{kind} media id is required.", code="VALIDATION_ERROR")

    service = MediaService()

    try:
        doc = service.get_media_doc(media_id)
        service.assert_user_can_manage(doc, user)

        if doc.status == "Deleted":
            return None, fail(f"{kind} media not found.", code="NOT_FOUND")

        if doc.purpose != purpose:
            return None, fail(f"{kind} media has the wrong purpose.", code="VALIDATION_ERROR")

        if doc.visibility != "Public":
            return None, fail(f"{kind} media must be public.", code="VALIDATION_ERROR")

        if doc.status == "Uploaded":
            return doc, None

        if doc.status == "Attached" and ad_name:
            if doc.attached_doctype == "AOS Ad" and doc.attached_name == ad_name:
                return doc, None

        return None, fail(f"{kind} media cannot be used in its current state.", code="VALIDATION_ERROR")

    except Exception as exc:
        return None, response_from_media_exception(exc, kind=kind)


def attach_ad_media(
    *,
    media_id: Any,
    user: str,
    purpose: str,
    ad_name: str,
    attached_field: str,
) -> Tuple[object | None, Any | None]:
    media_id = normalize_media_id(media_id)

    if not media_id:
        return None, None

    service = MediaService()

    try:
        doc = service.get_media_doc(media_id)
        service.assert_user_can_manage(doc, user)

        if doc.status == "Attached":
            if doc.attached_doctype == "AOS Ad" and doc.attached_name == ad_name:
                return doc, None
            return None, fail("Media is already attached.", code="VALIDATION_ERROR")

        if doc.status != "Uploaded":
            return None, fail("Media must be uploaded before it can be attached.", code="VALIDATION_ERROR")

        if doc.purpose != purpose:
            return None, fail("Media has the wrong purpose.", code="VALIDATION_ERROR")

        doc.status = "Attached"
        doc.attached_doctype = "AOS Ad"
        doc.attached_name = ad_name
        doc.attached_field = attached_field
        doc.attached_at = now_datetime()
        doc.save(ignore_permissions=True)
        return doc, None

    except Exception as exc:
        return None, response_from_media_exception(exc, kind="Ad")


def serialize_ad_media(media_id: Any, fallback_url: Any = "") -> Dict[str, Any] | None:
    media_id = normalize_media_id(media_id)
    url = get_media_public_url(media_id) if media_id else clean_str(fallback_url)

    if not media_id and not url:
        return None

    return {
        "media_id": media_id or None,
        "url": url,
    }
