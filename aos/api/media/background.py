"""Asynchronous Media background-removal API boundary."""

from __future__ import annotations

from aos.api.media.errors import media_error_response
from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.media.background_processing import (
    MediaProcessingService,
    serialize_processing_job,
)

from .constants import (
    BACKGROUND_PROCESSING_STATUS_LIMIT_PER_MINUTE_PER_USER,
    REMOVE_BACKGROUND_LIMIT_PER_MINUTE_PER_USER,
)


def remove_background_impl(**kwargs):
    """Queue idempotent background removal instead of blocking a web worker."""
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "remove_background", "user", user),
        ttl_seconds=60,
        limit=REMOVE_BACKGROUND_LIMIT_PER_MINUTE_PER_USER,
        message="Too many processing requests. Please try again shortly.",
    )
    if rl:
        return rl

    media_id = str(kwargs.get("media_id") or kwargs.get("id") or "").strip()
    if not media_id:
        return fail("Media id is required.", error="VALIDATION_ERROR")
    result_purpose = str(
        kwargs.get("result_purpose")
        or kwargs.get("output_purpose")
        or kwargs.get("purpose")
        or ""
    ).strip() or None

    try:
        service = MediaProcessingService()
        job = service.request_background_removal(
            user=user,
            source_media_id=media_id,
            result_purpose=result_purpose,
        )
        return ok(
            "Background removal queued.",
            data={"processing": serialize_processing_job(job, media_service=service.media)},
        )
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Remove Background Failed")


def processing_status_impl(**kwargs):
    """Return owner-scoped status/result for one durable Media processing job."""
    user, err = require_authenticated_user()
    if err:
        return err
    rl = rate_limit(
        key=rate_limit_key("media", "processing_status", "user", user),
        ttl_seconds=60,
        limit=BACKGROUND_PROCESSING_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many processing status requests. Please try again shortly.",
    )
    if rl:
        return rl
    job_id = str(kwargs.get("job_id") or kwargs.get("id") or "").strip()
    if not job_id:
        return fail("Processing job id is required.", error="VALIDATION_ERROR")
    try:
        service = MediaProcessingService()
        job = service.get_job(user=user, job_id=job_id)
        return ok(
            "Media processing status loaded.",
            data={"processing": serialize_processing_job(job, media_service=service.media)},
        )
    except Exception as exc:
        return media_error_response(exc, log_title="AOS Media Processing Status Failed")
