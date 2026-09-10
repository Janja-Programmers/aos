from __future__ import annotations

from aos.api.media.errors import media_error_response
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.media.media_service import MediaService, serialize_media_doc

from .constants import DELETE_MEDIA_LIMIT_PER_MINUTE_PER_USER
from .validators import require_media_id


def delete_media_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "delete", "user", user), ttl_seconds=60,
        limit=DELETE_MEDIA_LIMIT_PER_MINUTE_PER_USER,
        message="Too many delete requests. Please try again shortly.",
    )
    if rl:
        return rl
    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err
    if str(kwargs.get("force") or "").strip().lower() in {"1", "true", "yes", "on"}:
        return fail("Forced deletion is not available to clients.", error="MEDIA_ACCESS_DENIED")
    try:
        doc = MediaService().delete_media(media_id=media_id, user=user)
        return ok("Media deletion scheduled.", data={"media": serialize_media_doc(doc)})
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Delete Failed")
