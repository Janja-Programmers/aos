from __future__ import annotations

import frappe

from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
    serialize_media_doc,
)

from .constants import DELETE_MEDIA_LIMIT_PER_MINUTE_PER_USER
from .validators import require_media_id


def delete_media_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:media:delete:user:{user}",
        ttl_seconds=60,
        limit=DELETE_MEDIA_LIMIT_PER_MINUTE_PER_USER,
        message="Too many delete requests. Please try again shortly.",
    )
    if rl:
        return rl

    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err

    force = str(kwargs.get("force") or "").strip().lower() in {"1", "true", "yes", "on"}

    try:
        service = MediaService()
        doc = service.delete_media(media_id=media_id, user=user, force=force)

        return ok(
            "Media deleted.",
            data={"media": serialize_media_doc(doc)},
        )

    except MediaNotFoundError as exc:
        return fail(str(exc), code="NOT_FOUND")
    except MediaPermissionError as exc:
        return fail(str(exc), code="FORBIDDEN")
    except MediaValidationError as exc:
        return fail(str(exc), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Delete Failed")
        return fail("Failed to delete media.", code="INTERNAL_ERROR")
