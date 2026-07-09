"""Live cover media helpers.

Live cover images are public AOS Media Object records. ``AOS Live Stream.cover_image``
remains as a cached public URL for existing clients, while ``live_cover_media``
is the new source-of-truth relationship.
"""

from __future__ import annotations

from typing import Any

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

LIVE_COVER_PURPOSE = "live_cover"
LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_COVER_MEDIA_FIELD = "live_cover_media"


def clean_str(value: Any) -> str:
    return str(value or "").strip()


def normalize_media_id(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("media_id") or value.get("id") or value.get("name")
    return clean_str(value)


def looks_like_media_id(value: Any) -> bool:
    return normalize_media_id(value).startswith("MEDIA-")


def response_from_media_exception(exc: Exception):
    if isinstance(exc, MediaNotFoundError):
        return fail("Live cover media not found.", error="NOT_FOUND")

    if isinstance(exc, MediaPermissionError):
        return fail(safe_exception_message(exc, "Not allowed."), error="FORBIDDEN")

    if isinstance(exc, MediaValidationError):
        return fail(safe_exception_message(exc, "Invalid live cover media."), error="VALIDATION_ERROR")

    frappe.log_error(frappe.get_traceback(), "AOS Live Cover Media Failed")
    return fail("Failed to validate live cover media.", error="INTERNAL_ERROR")


def get_live_cover_media_id(live_id: str) -> str:
    if not live_id or not _live_has_cover_media_field():
        return ""

    return clean_str(
        frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            LIVE_COVER_MEDIA_FIELD,
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


def validate_live_cover_media_for_use(*, media_id: Any, user: str, live_id: str | None = None):
    """Validate that a live cover media object can be used by this host.

    New media must be Uploaded/unattached. Already attached media is allowed only
    when it is already attached to the same live stream.
    """
    media_id = normalize_media_id(media_id)

    if not media_id:
        return None, "", fail("Live cover media id is required.", error="VALIDATION_ERROR")

    service = MediaService()

    try:
        doc = service.get_media_doc(media_id)
        service.assert_user_can_manage(doc, user)

        if doc.status == "Deleted":
            return None, "", fail("Live cover media not found.", error="NOT_FOUND")

        if doc.purpose != LIVE_COVER_PURPOSE:
            return None, "", fail(
                "Live cover media has the wrong purpose.",
                error="VALIDATION_ERROR",
            )

        if doc.visibility != "Public":
            return None, "", fail(
                "Live cover media must be public.",
                error="VALIDATION_ERROR",
            )

        if doc.status == "Uploaded":
            return doc, _live_cover_media_url(doc), None

        if doc.status == "Attached":
            if live_id and doc.attached_doctype == LIVE_STREAM_DOCTYPE and doc.attached_name == live_id:
                return doc, _live_cover_media_url(doc), None

        return None, "", fail(
            "Live cover media cannot be used in its current state.",
            error="VALIDATION_ERROR",
        )

    except Exception as exc:
        return None, "", response_from_media_exception(exc)


def attach_live_cover_media(*, media_id: Any, user: str, live_id: str):
    media_id = normalize_media_id(media_id)
    doc, url, err = validate_live_cover_media_for_use(
        media_id=media_id,
        user=user,
        live_id=live_id,
    )
    if err:
        return None, "", err

    if doc.status != "Attached":
        doc.status = "Attached"
        doc.attached_doctype = LIVE_STREAM_DOCTYPE
        doc.attached_name = live_id
        doc.attached_field = LIVE_COVER_MEDIA_FIELD
        doc.attached_at = now_datetime()
        doc.save(ignore_permissions=True)

    return doc, url, None


def clear_live_cover_media(*, live_id: str, user: str) -> None:
    old_media_id = get_live_cover_media_id(live_id)
    if old_media_id:
        _mark_media_orphaned(old_media_id, owner_user=user)


def _live_cover_media_url(doc) -> str:
    if clean_str(getattr(doc, "public_url", None)):
        return clean_str(doc.public_url)
    return MediaService().get_url(media_id=doc.name, user=None)


def _live_has_cover_media_field() -> bool:
    try:
        return bool(frappe.get_meta(LIVE_STREAM_DOCTYPE).has_field(LIVE_COVER_MEDIA_FIELD))
    except Exception:
        return False


def _mark_media_orphaned(media_id: str, *, owner_user: str) -> None:
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
            "AOS Live Cover Media Release Failed",
        )
