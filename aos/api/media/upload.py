from __future__ import annotations

import frappe

from aos.api.media.errors import media_error_response
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.media.media_service import MediaService, serialize_media_doc
from aos.services.media.media_purposes import get_media_purpose

from .constants import (
    CONFIRM_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    INIT_UPLOAD_LIMIT_PER_MINUTE_PER_USER,
    MULTIPART_UPLOAD_CONTRACT_VERSION,
    MULTIPART_ABORT_LIMIT_PER_MINUTE_PER_USER,
    MULTIPART_COMPLETE_LIMIT_PER_MINUTE_PER_USER,
    MULTIPART_PART_URLS_LIMIT_PER_MINUTE_PER_USER,
    MULTIPART_STATUS_LIMIT_PER_MINUTE_PER_USER,
)
from .validators import require_media_id, require_upload_init_payload


def init_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "init", "user", user), ttl_seconds=60,
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
        policy = get_media_purpose(doc.purpose)
        limits = {
            "max_size_bytes": int(policy.max_size_bytes) if policy else None,
            "max_duration_seconds": int(policy.max_duration_seconds) if policy and policy.max_duration_seconds else None,
            "processing_required": bool(policy.processing_required) if policy else False,
            "duration_required": bool(
                policy and policy.processing_required and policy.max_duration_seconds
            ),
            "multipart_required_above_bytes": (
                int(policy.multipart_threshold_bytes)
                if policy and policy.multipart_threshold_bytes
                else None
            ),
            "multipart_part_size_bytes": (
                int(policy.multipart_part_size_bytes)
                if policy and policy.multipart_part_size_bytes
                else None
            ),
        }
        upload_mode = str(getattr(doc, "upload_mode", "direct") or "direct")
        multipart = None
        if upload_mode == "multipart":
            multipart = {
                "contract_version": MULTIPART_UPLOAD_CONTRACT_VERSION,
                # The public resumable-session identifier is the media id. The
                # object-store upload id intentionally never crosses this API.
                "session_id": doc.name,
                "part_size_bytes": int(getattr(doc, "multipart_part_size_bytes", 0) or 0),
                "part_count": int(getattr(doc, "multipart_part_count", 0) or 0),
                "part_url_batch_size": service.get_multipart_part_url_batch_size(),
                "max_parallel_parts": service.get_multipart_max_parallel_parts(),
                "part_url_expires_in": service.get_upload_expiry_minutes(policy) * 60,
                "session_expires_in": expires_in,
            }
        return ok("Upload initialized.", data={
            "media": serialize_media_doc(doc), "media_id": doc.name,
            "upload_contract_version": MULTIPART_UPLOAD_CONTRACT_VERSION,
            "upload_mode": upload_mode,
            "upload_url": upload_url, "upload_headers": upload_headers,
            "multipart": multipart,
            "expires_in": expires_in,
            "expires_at": getattr(doc, "upload_expires_at", None),
            "limits": limits,
        })
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Init Upload Failed")


def confirm_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "confirm", "user", user), ttl_seconds=60,
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


def multipart_part_urls_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "multipart", "urls", "user", user), ttl_seconds=60,
        limit=MULTIPART_PART_URLS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many multipart URL requests. Please try again shortly.",
    )
    if rl:
        return rl
    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err
    try:
        data = MediaService().get_multipart_part_urls(
            user=user,
            media_id=media_id,
            start_part=kwargs.get("start_part"),
            count=kwargs.get("count"),
        )
        data["contract_version"] = MULTIPART_UPLOAD_CONTRACT_VERSION
        return ok("Multipart upload URLs issued.", data=data)
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Multipart Part URLs Failed")


def multipart_status_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "multipart", "status", "user", user), ttl_seconds=60,
        limit=MULTIPART_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many multipart status requests. Please try again shortly.",
    )
    if rl:
        return rl
    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err
    try:
        data = MediaService().get_multipart_status(user=user, media_id=media_id)
        data["contract_version"] = MULTIPART_UPLOAD_CONTRACT_VERSION
        return ok(
            "Multipart upload status fetched.",
            data=data,
        )
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Multipart Status Failed")


def complete_multipart_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "multipart", "complete", "user", user), ttl_seconds=60,
        limit=MULTIPART_COMPLETE_LIMIT_PER_MINUTE_PER_USER,
        message="Too many multipart completion requests. Please try again shortly.",
    )
    if rl:
        return rl
    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err
    try:
        service = MediaService()
        doc = service.complete_multipart_upload(user=user, media_id=media_id)
        url = service.get_url(media_id=doc.name, user=user) if doc.visibility == "Public" else None
        return ok("Multipart upload completed.", data={
            "media": serialize_media_doc(doc, url=url),
            "media_id": doc.name,
            "url": url,
        })
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Multipart Complete Failed")


def abort_multipart_upload_impl(**kwargs):
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "multipart", "abort", "user", user), ttl_seconds=60,
        limit=MULTIPART_ABORT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many multipart abort requests. Please try again shortly.",
    )
    if rl:
        return rl
    media_id, err = require_media_id(kwargs.get("media_id") or kwargs.get("id"))
    if err:
        return err
    try:
        doc = MediaService().abort_multipart_upload(user=user, media_id=media_id)
        return ok("Multipart upload aborted.", data={
            "media": serialize_media_doc(doc),
            "media_id": doc.name,
        })
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Multipart Abort Failed")
