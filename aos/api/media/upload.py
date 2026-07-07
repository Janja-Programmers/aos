from __future__ import annotations

import frappe

from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
    serialize_media_doc,
)

from .constants import (
    CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
)
from .validators import invalid_purpose_response, require_media_id, require_upload_init_payload


def init_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:media:init:user:{user}",
        ttl_seconds=60,
        limit=INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many upload requests. Please try again shortly.",
    )
    if rl:
        return rl

    payload, err = require_upload_init_payload(kwargs)
    if err:
        return err

    try:
        service = MediaService()
        doc, upload_url, upload_headers, expires_in = service.init_upload(
            user=user,
            purpose=payload["purpose"],
            filename=payload["filename"],
            content_type=payload["content_type"],
            size_bytes=payload["size_bytes"],
        )

        return ok(
            "Upload initialized.",
            data={
                "media": serialize_media_doc(doc),
                "media_id": doc.name,
                "upload_url": upload_url,
                "upload_headers": upload_headers,
                "expires_in": expires_in,
            },
        )

    except MediaValidationError as exc:
        if str(exc) == "Invalid media purpose":
            return invalid_purpose_response()
        return safe_fail_from_exception(exc, fallback="Invalid request.", code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Init Upload Failed")
        return fail("Failed to initialize upload.", code="INTERNAL_ERROR")


def confirm_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:media:confirm:user:{user}",
        ttl_seconds=60,
        limit=CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many upload confirmations. Please try again shortly.",
    )
    if rl:
        return rl

    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err

    try:
        service = MediaService()
        doc = service.confirm_upload(user=user, media_id=media_id)

        url = None
        if doc.visibility == "Public":
            url = service.get_url(media_id=doc.name, user=user)

        return ok(
            "Upload confirmed.",
            data={
                "media": serialize_media_doc(doc, url=url),
                "media_id": doc.name,
                "url": url,
            },
        )

    except MediaNotFoundError as exc:
        return safe_fail_from_exception(exc, fallback="Resource not found.", code="NOT_FOUND")
    except MediaPermissionError as exc:
        return safe_fail_from_exception(exc, fallback="Not allowed.", code="FORBIDDEN")
    except MediaValidationError as exc:
        return safe_fail_from_exception(exc, fallback="Invalid request.", code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Confirm Upload Failed")
        return fail("Failed to confirm upload.", code="INTERNAL_ERROR")
