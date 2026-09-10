from __future__ import annotations

from aos.api.media.errors import media_error_response
from aos.api.shared.auth import optional_active_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.media.media_service import MediaService, serialize_media_doc

from .constants import GET_MEDIA_URL_LIMIT_PER_MINUTE_PER_USER
from .validators import require_media_id


def get_media_url_impl(**kwargs):
    user = optional_active_user()
    rl = rate_limit(
        key=(
            rate_limit_key("media", "url", "user", user)
            if user
            else rate_limit_key("media", "url", "ip", request_ip())
        ),
        ttl_seconds=60,
        limit=GET_MEDIA_URL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many media URL requests. Please try again shortly.",
    )
    if rl:
        return rl
    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err
    try:
        service = MediaService()
        doc = service.get_media_doc(media_id)
        url = service.get_url(
            media_id=media_id, user=user,
            expiry_minutes=kwargs.get("expiry_minutes") or kwargs.get("expires_minutes"),
        )
        return ok("Media URL fetched.", data={"media": serialize_media_doc(doc, url=url), "url": url})
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media URL Failed")
