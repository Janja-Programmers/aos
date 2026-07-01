from __future__ import annotations

import frappe

from aos.api.shared.auth import optional_active_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
    serialize_media_doc,
)

from .constants import GET_MEDIA_URL_LIMIT_PER_MINUTE_PER_USER
from .validators import require_media_id


def get_media_url_impl(**kwargs):
    user = optional_active_user()
    rate_key_user = user or "guest"

    rl = rate_limit(
        key=f"aos:media:url:user:{rate_key_user}",
        ttl_seconds=60,
        limit=GET_MEDIA_URL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many media URL requests. Please try again shortly.",
    )
    if rl:
        return rl

    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err

    expiry_minutes = kwargs.get("expiry_minutes") or kwargs.get("expires_minutes")

    try:
        service = MediaService()
        doc = service.get_media_doc(media_id)
        url = service.get_url(
            media_id=media_id,
            user=user,
            expiry_minutes=expiry_minutes,
        )

        return ok(
            "Media URL fetched.",
            data={"media": serialize_media_doc(doc, url=url), "url": url},
        )

    except MediaNotFoundError as exc:
        return fail(str(exc), code="NOT_FOUND")
    except MediaPermissionError as exc:
        return fail(str(exc), code="FORBIDDEN")
    except MediaValidationError as exc:
        return fail(str(exc), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media URL Failed")
        return fail("Failed to fetch media URL.", code="INTERNAL_ERROR")
