from __future__ import annotations

import frappe

from aos.api.media.errors import media_error_response
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.media.media_service import MediaService, serialize_media_doc

from .constants import CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER, INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER
from .validators import require_media_id, require_upload_init_payload


def init_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:media:init:user:{user}", ttl_seconds=60,
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
        doc, upload_url, upload_headers, expires_in = service.init_upload(user=user, **payload)
        return ok("Upload initialized.", data={
            "media": serialize_media_doc(doc), "media_id": doc.name,
            "upload_url": upload_url, "upload_headers": upload_headers,
            "expires_in": expires_in,
            "expires_at": getattr(doc, "upload_expires_at", None),
        })
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Init Upload Failed")


def confirm_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=f"aos:media:confirm:user:{user}", ttl_seconds=60,
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
        url = service.get_url(media_id=doc.name, user=user) if doc.visibility == "Public" else None
        return ok("Upload confirmed.", data={
            "media": serialize_media_doc(doc, url=url), "media_id": doc.name, "url": url,
        })
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Confirm Upload Failed")
