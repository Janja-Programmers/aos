"""Durable Frappe-side orchestration for internal Shorts video processing.

Shorts owns product state, Media owns storage identity, and the private video
companion owns bounded FFmpeg work.  Durable jobs plus the shared transactional
outbox make dispatch retry-safe across process/node failures without request-flow
commits.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any

import frappe
import requests
from frappe.utils import add_to_date, now_datetime

from aos.services.media.media_purposes import get_media_purpose
from aos.services.media.media_service import MediaService
from aos.services.shorts.classification import reclassify_short
from aos.services.shorts.constants import (
    CONTENT_TYPE_VIDEO,
    LIFECYCLE_FAILED,
    LIFECYCLE_PENDING_REVIEW,
    LIFECYCLE_PROCESSING,
    PROCESSING_FAILED,
    PROCESSING_READY,
    PROCESSING_RETRY_WAITING,
    REUSE_SEGMENT,
    REUSE_SIDE_BY_SIDE,
    SOUND_ACTIVE,
    SOUND_SOURCE_ORIGINAL,
)
from aos.services.transactional_outbox import (
    OutboxConflictError,
    current_outbox_dispatch_context,
    ensure_outbox_for_job,
    mark_outbox_callback,
    outbox_dispatch_context,
    record_companion_dispatch_outcome,
    sanitized_dispatch_error,
    validate_callback_idempotency,
)
from aos.utils.aos_config import clean_url, get_env, get_env_int, get_first_env


class VideoProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoProcessingConfig:
    service_url: str
    service_secret: str = field(repr=False)
    callback_secret: str = field(repr=False)
    callback_url: str
    request_timeout_seconds: int
    max_attempts: int
    queue: str
    dispatcher_timeout_seconds: int


@dataclass(frozen=True)
class _CallbackCorrelationJob:
    doctype: str
    name: str
    idempotency_key: str


@dataclass(frozen=True)
class MissingVideoProcessingCallbackResult:
    name: str
    short: str
    status: str


def get_max_short_duration_seconds() -> int:
    return get_env_int("VIDEO_MAX_DURATION_SECONDS", 600, min_value=1, max_value=3600)


def get_video_processing_config() -> VideoProcessingConfig:
    service_url = clean_url(
        get_first_env(
            "VIDEO_SERVICE_URL",
            default=f"http://127.0.0.1:{get_env('VIDEO_SERVICE_PORT', '8130')}",
        ),
        default="http://127.0.0.1:8130",
    )
    callback_url = clean_url(get_env("VIDEO_CALLBACK_URL"))
    if not callback_url:
        domain = get_env("AOS_API_DOMAIN")
        callback_url = (
            f"https://{domain}/api/method/aos.api.internal.video_processing.handle_callback"
            if domain
            else "http://127.0.0.1:8000/api/method/aos.api.internal.video_processing.handle_callback"
        )
    return VideoProcessingConfig(
        service_url=service_url,
        service_secret=get_env("VIDEO_SERVICE_SECRET", "") or "",
        callback_secret=get_env("VIDEO_SERVICE_CALLBACK_SECRET", "") or "",
        callback_url=callback_url,
        request_timeout_seconds=get_env_int(
            "VIDEO_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120
        ),
        max_attempts=get_env_int("VIDEO_MAX_RETRIES", 5, min_value=1, max_value=10),
        queue=get_env("VIDEO_FRAPPE_QUEUE", "long") or "long",
        dispatcher_timeout_seconds=get_env_int(
            "VIDEO_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800
        ),
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()


def build_signature(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(str(secret or "").encode(), payload, hashlib.sha256).hexdigest()


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    return bool(
        secret
        and signature
        and hmac.compare_digest(build_signature(secret, payload), str(signature).strip())
    )


def _lock_short(short_id: str):
    rows = frappe.db.sql(
        """SELECT name, owner, content_type, lifecycle_status, processing_status,
                  processing_generation, raw_video_media, playback_media,
                  source_short, reuse_type, source_start_ms, source_end_ms
             FROM `tabAOS Short` WHERE name=%s LIMIT 1 FOR UPDATE""",
        (short_id,),
        as_dict=True,
    )
    if not rows:
        raise VideoProcessingError("Short not found")
    return rows[0]


def _media_input(media_id: str) -> dict[str, Any]:
    row = frappe.db.get_value(
        "AOS Media Object",
        media_id,
        ["name", "bucket", "object_key", "content_type", "size_bytes", "original_filename", "status"],
        as_dict=True,
    )
    if not row or str(row.status) not in {"Uploaded", "Processing", "Ready", "Attached"}:
        raise VideoProcessingError("Source Media is not ready")
    return {
        "media_id": str(row.name),
        "bucket": str(row.bucket),
        "object_key": str(row.object_key),
        "content_type": str(row.content_type or ""),
        "size_bytes": int(row.size_bytes or 0),
        "filename": str(row.original_filename or ""),
    }


def _purpose_output(purpose: str) -> tuple[str, str]:
    rule = get_media_purpose(purpose)
    if not rule:
        raise VideoProcessingError(f"Unknown Media purpose: {purpose}")
    service = MediaService()
    return service.storage.bucket_for_type(rule.bucket_type), rule.prefix


def _operation(short: Any, requested: str) -> str:
    if requested == "Process":
        reuse = str(short.reuse_type or "")
        if reuse == REUSE_SIDE_BY_SIDE:
            return REUSE_SIDE_BY_SIDE
        if reuse == REUSE_SEGMENT:
            return REUSE_SEGMENT
    return requested


def enqueue_video_processing_dispatch(video_processing_job_id: str | Any) -> Any:
    """Persist Video Processing dispatch intent in the caller transaction.

    This helper is internal-only. It deliberately never commits; the shared
    transactional outbox publishes after the surrounding transaction is durable.
    """
    job = (
        video_processing_job_id
        if getattr(video_processing_job_id, "doctype", None) == "AOS Video Processing Job"
        else frappe.get_doc("AOS Video Processing Job", str(video_processing_job_id or "").strip())
    )
    config = get_video_processing_config()
    return ensure_outbox_for_job(
        service_type="video_processing",
        job=job,
        queue=config.queue,
        timeout_seconds=config.dispatcher_timeout_seconds,
        aggregate_doctype="AOS Short",
        aggregate_name=job.short,
        max_attempts=max(1, int(job.max_attempts or config.max_attempts)),
    )


def create_video_processing_job(
    short_id: str,
    *,
    operation: str = "Process",
    force: bool = False,
    reason: str = "short_publish",
    client_idempotency_key: str | None = None,
    enqueue: bool = True,
):
    """Create one durable processing attempt in the caller's transaction."""
    short = _lock_short(str(short_id or "").strip())
    if str(short.content_type) != CONTENT_TYPE_VIDEO:
        raise VideoProcessingError("Only Video Shorts require video processing")
    if str(short.lifecycle_status) == "Deleted":
        raise VideoProcessingError("Deleted Short cannot be processed")

    operation = _operation(short, str(operation or "Process"))
    if operation not in {"Process", "Download", "Side By Side", "Segment"}:
        raise VideoProcessingError("Invalid video processing operation")

    source_media = str(short.playback_media if operation == "Download" else short.raw_video_media or "")
    if not source_media:
        raise VideoProcessingError("Required source Media is missing")

    if operation == "Download":
        # One cached download rendition is enough. Concurrent/retried requests
        # converge on the existing active job instead of spawning transcodes.
        existing = frappe.db.get_value(
            "AOS Video Processing Job",
            {"short": short.name, "operation": "Download", "status": ["in", ["Queued", "Processing", "Retry Waiting"]]},
            "name",
        )
        if existing:
            return frappe.get_doc("AOS Video Processing Job", existing)
        generation = int(
            frappe.db.sql(
                "SELECT COALESCE(MAX(generation),0)+1 FROM `tabAOS Video Processing Job` WHERE short=%s AND operation='Download'",
                (short.name,),
            )[0][0]
            or 1
        )
    else:
        current_generation = max(0, int(short.processing_generation or 0))
        # First submission owns generation 1. Explicit retry/reprocessing always
        # advances the generation so an older callback becomes a safe no-op.
        generation = current_generation + 1 if (force or current_generation == 0) else current_generation

    active_key = f"{short.name}:{operation}:{generation}"
    existing = frappe.db.get_value(
        "AOS Video Processing Job",
        {"active_key": active_key, "status": ["in", ["Queued", "Processing", "Retry Waiting"]]},
        "name",
    )
    if existing:
        return frappe.get_doc("AOS Video Processing Job", existing)

    if operation != "Download":
        frappe.db.set_value(
            "AOS Short", short.name,
            {
                "processing_generation": generation,
                "processing_status": "Queued",
                "lifecycle_status": LIFECYCLE_PROCESSING,
                "processing_error": None,
            },
            update_modified=True,
        )

    job = frappe.get_doc(
        {
            "doctype": "AOS Video Processing Job",
            "short": short.name,
            "raw_video_media": source_media,
            "source_short": str(short.source_short or "") or None,
            "operation": operation,
            "status": "Queued",
            "generation": generation,
            "idempotency_key": hashlib.sha256(
                f"aos:video:{active_key}:{str(client_idempotency_key or '').strip()[:128]}".encode()
            ).hexdigest(),
            "attempt_count": 0,
            "max_attempts": get_video_processing_config().max_attempts,
            "active_key": active_key,
        }
    )
    job.insert(ignore_permissions=True)
    if enqueue:
        enqueue_video_processing_dispatch(job)
    return job



def _sound_input(short_id: str) -> dict[str, Any] | None:
    rows = frappe.db.sql(
        """SELECT ss.sound, ss.start_ms, ss.duration_ms, ss.volume,
                  ss.is_original_audio, s.sound_media, s.status, s.reuse_allowed
             FROM `tabAOS Short Sound` ss
             INNER JOIN `tabAOS Sound` s ON s.name=ss.sound
            WHERE ss.short=%s LIMIT 1""",
        (short_id,),
        as_dict=True,
    )
    if not rows:
        return None
    row = rows[0]
    if int(row.is_original_audio or 0):
        return None
    if str(row.status) != SOUND_ACTIVE or not int(row.reuse_allowed or 0):
        raise VideoProcessingError("Selected Sound is unavailable")
    base = _media_input(str(row.sound_media))
    base.update(
        {
            "sound_id": str(row.sound),
            "start_ms": int(row.start_ms or 0),
            "duration_ms": int(row.duration_ms or 0),
            "volume": float(row.volume or 1.0),
        }
    )
    return base


def build_video_job_payload(job) -> dict[str, Any]:
    short = frappe.db.get_value(
        "AOS Short",
        job.short,
        ["name", "owner", "source_short", "source_start_ms", "source_end_ms", "playback_media"],
        as_dict=True,
    )
    if not short:
        raise VideoProcessingError("Short not found")

    playback_bucket, playback_prefix = _purpose_output("short_video_playback")
    poster_bucket, poster_prefix = _purpose_output("short_poster")
    storyboard_bucket, storyboard_prefix = _purpose_output("short_storyboard")
    download_bucket, download_prefix = _purpose_output("short_download")
    sound_bucket, sound_prefix = _purpose_output("short_original_audio")

    source_video = None
    if str(job.operation) in {"Side By Side", "Segment"}:
        source_playback = (
            frappe.db.get_value("AOS Short", short.source_short, "playback_media")
            if short.source_short
            else None
        )
        if not source_playback:
            raise VideoProcessingError("Reusable source Short is not ready")
        source_video = _media_input(str(source_playback))

    profile = frappe.db.get_value("AOS Profile", {"user": short.owner}, ["display_name"], as_dict=True)
    return {
        **outbox_dispatch_context(job_doctype="AOS Video Processing Job", job_name=job.name),
        "job_id": job.name,
        "idempotency_key": job.idempotency_key,
        "job_generation": int(job.generation or 1),
        "short_id": job.short,
        "operation": str(job.operation),
        "callback_url": get_video_processing_config().callback_url,
        "raw_video": _media_input(str(job.raw_video_media)),
        "sound": None if str(job.operation) == "Download" else _sound_input(job.short),
        "source_video": source_video,
        "source_start_ms": int(short.source_start_ms or 0),
        "source_end_ms": int(short.source_end_ms or 0),
        "creator_label": str((profile or {}).get("display_name") or "Creator")[:140],
        "output": {
            "playback_bucket": playback_bucket,
            "playback_base_path": playback_prefix,
            "poster_bucket": poster_bucket,
            "poster_base_path": poster_prefix,
            "storyboard_bucket": storyboard_bucket,
            "storyboard_base_path": storyboard_prefix,
            "download_bucket": download_bucket,
            "download_base_path": download_prefix,
            "sound_bucket": sound_bucket,
            "sound_base_path": sound_prefix,
            "max_duration_seconds": get_max_short_duration_seconds(),
        },
    }


def dispatch_video_processing_job(job_id: str):
    """Outbox worker dispatch; never performs FFmpeg and never manually commits."""
    job = frappe.get_doc("AOS Video Processing Job", job_id)
    context = current_outbox_dispatch_context(
        job_doctype="AOS Video Processing Job", job_name=job.name
    )
    if str(job.status) in {"Ready", "Failed", "Cancelled"}:
        return job
    if str(job.status) == "Retry Waiting":
        return job
    if str(job.status) == "Processing" and job.service_job_id and not (
        context and context.recovery_dispatch
    ):
        return job

    config = get_video_processing_config()
    if not config.service_secret:
        raise VideoProcessingError("Video service secret is not configured")

    previous = int(job.attempt_count or 0)
    job.status = "Processing"
    job.dispatched_at = now_datetime()
    job.started_at = job.started_at or now_datetime()
    job.last_error = None
    job.lease_owner = f"outbox:{job.name}"
    job.lease_expires_at = add_to_date(now_datetime(), seconds=3600)
    job.save(ignore_permissions=True)

    payload = build_video_job_payload(job)
    job.request_payload = json.dumps(payload, default=str, separators=(",", ":"))
    job.save(ignore_permissions=True)
    body = _json_bytes(payload)
    try:
        response = requests.post(
            f"{config.service_url}/jobs",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-AOS-Signature": build_signature(config.service_secret, body),
                "Idempotency-Key": job.idempotency_key,
            },
            timeout=config.request_timeout_seconds,
        )
        # HTTP status inspection is diagnostic only. Durable-outbox tests and
        # alternative request adapters may provide the normal response contract
        # (raise_for_status/json/content) without exposing ``status_code``.
        # Never make a successful dispatch depend on this optional attribute.
        if getattr(response, "status_code", None) == 422:
            fields: list[str] = []
            try:
                rejected = response.json()
                rows = ((rejected.get("data") or {}).get("fields") or []) if isinstance(rejected, dict) else []
                fields = [str(row.get("field") or "")[:80] for row in rows if isinstance(row, dict) and row.get("field")][:20]
            except Exception:
                fields = []
            frappe.log_error(
                "Video processing companion rejected request validation fields: " + (", ".join(fields) if fields else "unknown"),
                "Video processing request validation rejected",
            )
        response.raise_for_status()
        data = response.json() if response.content else {}
        action = record_companion_dispatch_outcome(str(data.get("dispatch_action") or ""), data)
        job.reload()
        job.attempt_count = previous + 1 if action in {"enqueued", "stale_generation_replaced"} else previous
        job.status = "Processing"
        job.service_job_id = str(data.get("service_job_id") or data.get("job_id") or job.service_job_id or "")
        job.response_payload = json.dumps(data, default=str, separators=(",", ":"))
        job.save(ignore_permissions=True)
        return job
    except OutboxConflictError as exc:
        job.reload()
        job.last_error_code = exc.error_code
        job.last_error = exc.error_code
        job.save(ignore_permissions=True)
        raise
    except Exception as exc:
        code = sanitized_dispatch_error(exc)
        job.reload()
        # A transport error can happen after downstream accepted the idempotent job.
        # The outbox owns reconciliation and safe redispatch.
        job.status = "Processing"
        job.last_error_code = code
        job.last_error = code
        job.save(ignore_permissions=True)
        frappe.log_error(frappe.get_traceback(), f"Video processing dispatch failed: {code}")
        raise


def _register_asset(
    *, short: Any, job: Any, metadata: dict[str, Any], purpose: str,
    fieldname: str, filename_default: str, duration: float | None = None,
) -> str | None:
    if not isinstance(metadata, dict):
        return None
    bucket = str(metadata.get("bucket") or "").strip()
    key = str(metadata.get("object_key") or "").strip()
    content_type = str(metadata.get("content_type") or "").strip()
    if not bucket or not key or not content_type:
        return None
    service = MediaService()
    previous_media_id = str(
        frappe.db.get_value("AOS Short", short.name, fieldname) or ""
    ).strip()
    doc = service.create_uploaded_from_existing_object(
        user=str(short.owner),
        purpose=purpose,
        filename=str(metadata.get("filename") or filename_default),
        content_type=content_type,
        bucket=bucket,
        object_key=key,
        size_bytes=int(metadata.get("size_bytes") or 0) or None,
        etag=str(metadata.get("etag") or "") or None,
        width=int(metadata.get("width") or 0) or None,
        height=int(metadata.get("height") or 0) or None,
        duration_seconds=duration,
        derived_from_media=str(job.raw_video_media),
    )
    service.attach_media(
        media_id=doc.name,
        user=str(short.owner),
        purpose=purpose,
        attached_doctype="AOS Short",
        attached_name=short.name,
        attached_field=fieldname,
        replacing_media_id=previous_media_id or None,
        system=True,
    )
    if previous_media_id and previous_media_id != str(doc.name):
        service.release_media(
            media_id=previous_media_id,
            user=str(short.owner),
            attached_doctype="AOS Short",
            attached_name=short.name,
            replacement_media_id=str(doc.name),
            system=True,
        )
    values: dict[str, Any] = {fieldname: doc.name}
    if fieldname == "poster_media":
        current_cover = str(
            frappe.db.get_value("AOS Short", short.name, "cover_media") or ""
        ).strip()
        if not current_cover or current_cover == previous_media_id:
            values["cover_media"] = doc.name
    frappe.db.set_value("AOS Short", short.name, values, update_modified=False)
    return str(doc.name)


def _ensure_original_sound(short: Any, metadata: dict[str, Any], duration: float) -> None:
    if not isinstance(metadata, dict) or not metadata:
        return
    current_relation = frappe.db.get_value(
        "AOS Short Sound",
        {"short": short.name},
        ["name", "sound", "is_original_audio"],
        as_dict=True,
    )
    if current_relation and not int(current_relation.is_original_audio or 0):
        return

    existing_sound_id = (
        str(current_relation.sound)
        if current_relation and current_relation.sound
        else str(
            frappe.db.get_value(
                "AOS Sound",
                {"created_from_short": short.name, "source_type": SOUND_SOURCE_ORIGINAL},
                "name",
            )
            or ""
        )
    )
    service = MediaService()
    media = service.create_uploaded_from_existing_object(
        user=str(short.owner),
        purpose="short_original_audio",
        filename=str(metadata.get("filename") or f"{short.name}_original.m4a"),
        content_type=str(metadata.get("content_type") or "audio/mp4"),
        bucket=str(metadata.get("bucket") or ""),
        object_key=str(metadata.get("object_key") or ""),
        size_bytes=int(metadata.get("size_bytes") or 0) or None,
        etag=str(metadata.get("etag") or "") or None,
        duration_seconds=duration,
        derived_from_media=str(short.raw_video_media),
    )
    account = frappe.db.get_value("AOS Profile", {"user": short.owner}, "name")
    if existing_sound_id:
        old_media_id = str(
            frappe.db.get_value("AOS Sound", existing_sound_id, "sound_media") or ""
        ).strip()
        service.attach_media(
            media_id=media.name,
            user=str(short.owner),
            purpose="short_original_audio",
            attached_doctype="AOS Sound",
            attached_name=existing_sound_id,
            attached_field="sound_media",
            replacing_media_id=old_media_id or None,
            system=True,
        )
        if old_media_id and old_media_id != str(media.name):
            service.release_media(
                media_id=old_media_id,
                user=str(short.owner),
                attached_doctype="AOS Sound",
                attached_name=existing_sound_id,
                replacement_media_id=str(media.name),
                system=True,
            )
        frappe.db.set_value(
            "AOS Sound",
            existing_sound_id,
            {"sound_media": media.name, "duration_seconds": duration},
            update_modified=False,
        )
        sound_id = existing_sound_id
    else:
        creator = frappe.db.get_value("AOS Profile", account, "display_name") if account else None
        sound = frappe.get_doc(
            {
                "doctype": "AOS Sound",
                "title": f"Original sound - {creator or 'Creator'}"[:140],
                "artist": "",
                "source_type": SOUND_SOURCE_ORIGINAL,
                "status": SOUND_ACTIVE,
                "sound_media": media.name,
                "duration_seconds": duration,
                "creator_account": account,
                "created_from_short": short.name,
                "reuse_allowed": 1,
            }
        )
        sound.insert(ignore_permissions=True)
        sound_id = str(sound.name)
        service.attach_media(
            media_id=media.name,
            user=str(short.owner),
            purpose="short_original_audio",
            attached_doctype="AOS Sound",
            attached_name=sound_id,
            attached_field="sound_media",
            system=True,
        )

    if not current_relation:
        frappe.get_doc(
            {
                "doctype": "AOS Short Sound",
                "short": short.name,
                "sound": sound_id,
                "start_ms": 0,
                "duration_ms": int(duration * 1000),
                "volume": 1.0,
                "is_original_audio": 1,
            }
        ).insert(ignore_permissions=True)


def _schedule_retry(job: Any, short: Any, error: str) -> Any | None:
    attempts = max(int(job.attempt_count or 0), 1)
    max_attempts = max(1, int(job.max_attempts or 5))
    message = str(error or "Video processing failed")[:1000]

    job.status = "Failed"
    job.last_error_code = "VIDEO_PROCESSING_FAILED"
    job.last_error = message
    job.callback_received_at = now_datetime()
    job.completed_at = now_datetime()
    job.active_key = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.save(ignore_permissions=True)

    if attempts >= max_attempts:
        if str(job.operation) != "Download":
            frappe.db.set_value(
                "AOS Short", short.name,
                {
                    "processing_status": PROCESSING_FAILED,
                    "lifecycle_status": LIFECYCLE_FAILED,
                    "processing_error": message,
                },
                update_modified=True,
            )
        return None

    delay = min(3600, 60 * (2 ** max(0, attempts - 1)))
    generation = int(job.generation or 0) + 1
    if str(job.operation) != "Download":
        generation = max(generation, int(short.processing_generation or 0) + 1)
        frappe.db.set_value(
            "AOS Short", short.name,
            {
                "processing_generation": generation,
                "processing_status": PROCESSING_RETRY_WAITING,
                "lifecycle_status": LIFECYCLE_PROCESSING,
                "processing_error": message,
            },
            update_modified=True,
        )
    source_media = str(short.playback_media if str(job.operation) == "Download" else short.raw_video_media or "")
    active_key = f"{short.name}:{job.operation}:{generation}"
    retry = frappe.get_doc(
        {
            "doctype": "AOS Video Processing Job",
            "short": short.name,
            "raw_video_media": source_media,
            "source_short": str(short.source_short or "") or None,
            "operation": str(job.operation),
            "status": "Retry Waiting",
            "generation": generation,
            "idempotency_key": hashlib.sha256(f"aos:video:{active_key}".encode()).hexdigest(),
            "attempt_count": attempts,
            "max_attempts": max_attempts,
            "next_retry_at": add_to_date(now_datetime(), seconds=delay),
            "active_key": active_key,
        }
    )
    retry.insert(ignore_permissions=True)
    return retry


def _missing_job_callback_status(outbox: Any) -> str:
    callback = str(getattr(outbox, "callback_status", None) or "").strip().lower()
    if callback == "cancelled":
        return "Cancelled"
    if str(getattr(outbox, "status", None) or "") == "Completed With Failure" or callback == "failed":
        return "Failed"
    return "Ready"


def _handle_missing_video_processing_job_callback(
    payload: dict[str, Any], *, callback_status: str
) -> MissingVideoProcessingCallbackResult:
    """Converge a signed late callback after its durable job row disappeared.

    The transactional outbox is the durable dispatch correlation record.  We only
    acknowledge a missing-job callback after the outbox validates its stable
    dispatch id, generation and token.  Deleted/obsolete Short generations become
    harmless cancellations.  A missing job for the current processing generation
    is an infrastructure inconsistency, so the Short is failed deterministically
    and can be retried through the normal creator retry flow instead of generating
    an endless 502 callback loop.
    """
    job_id = str(payload.get("job_id") or "").strip()
    short_id = str(payload.get("short_id") or "").strip()
    correlation_job = _CallbackCorrelationJob(
        doctype="AOS Video Processing Job",
        name=job_id,
        idempotency_key=str(payload.get("idempotency_key") or "").strip(),
    )
    validation = validate_callback_idempotency(
        correlation_job, payload, callback_status=callback_status
    )
    if not validation.outbox_name:
        raise VideoProcessingError("Video processing job not found")

    outbox = frappe.get_doc("AOS Transactional Outbox", validation.outbox_name, for_update=True)
    if (
        str(outbox.aggregate_doctype or "") != "AOS Short"
        or not short_id
        or str(outbox.aggregate_name or "") != short_id
    ):
        raise OutboxConflictError(
            "Callback Short does not match the durable outbox aggregate.",
            error_code="STABLE_DISPATCH_MISMATCH",
            outbox_name=outbox.name,
        )

    if validation.duplicate:
        return MissingVideoProcessingCallbackResult(
            name=job_id, short=short_id, status=_missing_job_callback_status(outbox)
        )

    dispatch_generation = int(payload.get("dispatch_generation") or 0)
    dispatch_token = str(payload.get("dispatch_token") or "").strip()
    short_exists = bool(frappe.db.exists("AOS Short", short_id))
    if not short_exists:
        mark_outbox_callback(
            job_doctype="AOS Video Processing Job",
            job_name=job_id,
            callback_status="cancelled",
            success=True,
            dispatch_token=dispatch_token,
            dispatch_generation=dispatch_generation,
        )
        return MissingVideoProcessingCallbackResult(name=job_id, short=short_id, status="Cancelled")

    short = frappe.get_doc("AOS Short", short_id, for_update=True)
    try:
        supplied_generation = int(payload.get("job_generation") or 0)
    except (TypeError, ValueError):
        supplied_generation = 0
    current_generation = int(short.processing_generation or 0)
    if str(short.lifecycle_status or "") == "Deleted" or (
        supplied_generation > 0 and current_generation > supplied_generation
    ):
        mark_outbox_callback(
            job_doctype="AOS Video Processing Job",
            job_name=job_id,
            callback_status="cancelled",
            success=True,
            dispatch_token=dispatch_token,
            dispatch_generation=dispatch_generation,
        )
        return MissingVideoProcessingCallbackResult(name=job_id, short=short_id, status="Cancelled")

    operation = str(payload.get("operation") or "").strip()
    looks_like_publish_processing = operation in {"Process", "Side By Side", "Segment"}
    if not operation:
        looks_like_publish_processing = (
            str(short.lifecycle_status or "") == LIFECYCLE_PROCESSING
            or str(short.processing_status or "") in {"Queued", "Processing", "Retry Waiting"}
        )

    error = str(payload.get("error") or "PROCESSING_JOB_MISSING").strip()[:500]
    if looks_like_publish_processing and (
        supplied_generation <= 0 or supplied_generation == current_generation
    ):
        frappe.db.set_value(
            "AOS Short",
            short.name,
            {
                "processing_status": PROCESSING_FAILED,
                "lifecycle_status": LIFECYCLE_FAILED,
                "processing_error": "PROCESSING_JOB_MISSING",
            },
            update_modified=True,
        )

    mark_outbox_callback(
        job_doctype="AOS Video Processing Job",
        job_name=job_id,
        callback_status=callback_status or "failed",
        success=False,
        error=error or "PROCESSING_JOB_MISSING",
        dispatch_token=dispatch_token,
        dispatch_generation=dispatch_generation,
    )
    frappe.logger("aos.callbacks", allow_site=True).warning(
        "Signed video processing callback converged without durable job: job=%s short=%s operation=%s",
        job_id,
        short_id,
        operation or "unknown",
    )
    return MissingVideoProcessingCallbackResult(name=job_id, short=short_id, status="Failed")


def handle_video_processing_callback(payload: dict[str, Any]):
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise VideoProcessingError("Video processing job not found")
    incoming = str(payload.get("status") or "").strip().lower()
    callback_status = "completed" if incoming in {"ready", "completed"} else incoming
    if not frappe.db.exists("AOS Video Processing Job", job_id):
        return _handle_missing_video_processing_job_callback(payload, callback_status=callback_status)
    job = frappe.get_doc("AOS Video Processing Job", job_id, for_update=True)
    validation = validate_callback_idempotency(job, payload, callback_status=callback_status)
    if validation.duplicate:
        return job

    # A terminal durable job may still be invoked directly by internal recovery
    # or focused tests without a correlated outbox row. Preserve idempotency for
    # the same terminal result and reject a conflicting late result before any
    # Short/domain lookup or mutation. Correlated callbacks are still validated
    # above against dispatch generation/token first.
    terminal = str(job.status or "")
    same_terminal = (
        (terminal == "Ready" and incoming in {"ready", "completed"})
        or (terminal == "Failed" and incoming == "failed")
        or (terminal == "Cancelled" and incoming == "cancelled")
    )
    if terminal in {"Ready", "Failed", "Cancelled"}:
        if same_terminal:
            return job
        raise VideoProcessingError(f"Video processing job is already {terminal}")

    short = frappe.get_doc("AOS Short", job.short, for_update=True)
    supplied_generation = int(payload.get("job_generation") or job.generation or 0)
    if supplied_generation != int(job.generation or 0) or (
        str(job.operation) != "Download"
        and int(short.processing_generation or 0) != int(job.generation or 0)
    ):
        job.status = "Cancelled"
        job.active_key = None
        job.completed_at = now_datetime()
        job.callback_received_at = now_datetime()
        job.lease_owner = None
        job.lease_expires_at = None
        job.save(ignore_permissions=True)
        mark_outbox_callback(
            job_doctype="AOS Video Processing Job", job_name=job.name,
            callback_status="completed", success=True,
        )
        return job

    if str(short.lifecycle_status) == "Deleted":
        job.status = "Cancelled"
        job.active_key = None
        job.completed_at = now_datetime()
        job.lease_owner = None
        job.lease_expires_at = None
        job.save(ignore_permissions=True)
        mark_outbox_callback(
            job_doctype="AOS Video Processing Job", job_name=job.name,
            callback_status="completed", success=True,
        )
        return job

    if incoming == "failed":
        error = str(payload.get("error") or "Video processing failed")
        _schedule_retry(job, short, error)
        mark_outbox_callback(
            job_doctype="AOS Video Processing Job", job_name=job.name,
            callback_status="failed", success=False, error=error,
        )
        return job
    if incoming not in {"ready", "completed"}:
        raise VideoProcessingError("Invalid video processing callback status")

    duration = float(payload.get("duration_seconds") or short.duration_seconds or 0)
    if duration < 0 or duration > get_max_short_duration_seconds():
        raise VideoProcessingError("Invalid processed duration")
    outputs = payload.get("outputs") if isinstance(payload.get("outputs"), dict) else {}

    if str(job.operation) == "Download":
        media_id = _register_asset(
            short=short, job=job, metadata=outputs.get("download") or {},
            purpose="short_download", fieldname="download_media",
            filename_default=f"{short.name}_AOS.mp4", duration=duration,
        )
        if not media_id:
            raise VideoProcessingError("Download output missing")
    else:
        playback = _register_asset(
            short=short, job=job, metadata=outputs.get("playback") or {},
            purpose="short_video_playback", fieldname="playback_media",
            filename_default=f"{short.name}.mp4", duration=duration,
        )
        manifest = _register_asset(
            short=short, job=job, metadata=outputs.get("manifest") or {},
            purpose="short_video_manifest", fieldname="playback_manifest_media",
            filename_default="master.m3u8",
        )
        poster = _register_asset(
            short=short, job=job, metadata=outputs.get("poster") or {},
            purpose="short_poster", fieldname="poster_media",
            filename_default=f"{short.name}_poster.jpg",
        )
        _register_asset(
            short=short, job=job, metadata=outputs.get("storyboard") or {},
            purpose="short_storyboard", fieldname="storyboard_media",
            filename_default=f"{short.name}_storyboard.jpg",
        )
        _register_asset(
            short=short, job=job, metadata=outputs.get("storyboard_manifest") or {},
            purpose="short_storyboard_manifest", fieldname="storyboard_manifest_media",
            filename_default=f"{short.name}_storyboard.json",
        )
        if not playback or not manifest or not poster:
            raise VideoProcessingError("Required video outputs missing")

        frappe.db.set_value(
            "AOS Short", short.name,
            {"duration_seconds": duration, "processing_status": PROCESSING_READY, "processing_error": None},
            update_modified=True,
        )
        _ensure_original_sound(short, outputs.get("original_audio") or {}, duration)
        classification = payload.get("classification") if isinstance(payload.get("classification"), dict) else {}
        reclassify_short(
            short.name,
            visual_scores=classification.get("scores"),
            source="video",
            model_version=str(classification.get("model_version") or classification.get("model") or "video-v1")[:140],
        )
        fresh = frappe.get_doc("AOS Short", short.name, for_update=True)
        if str(fresh.lifecycle_status) == LIFECYCLE_PROCESSING:
            fresh.lifecycle_status = LIFECYCLE_PENDING_REVIEW
            fresh.moderation_status = "Pending"
            fresh.moderation_generation = int(fresh.moderation_generation or 0) + 1
            fresh.save(ignore_permissions=True)
            from aos.services.moderation_service import enqueue_short_moderation
            enqueue_short_moderation(fresh.name, source="short_processing_ready", was_visible=False)

    job.status = "Ready"
    job.response_payload = json.dumps(payload, default=str, separators=(",", ":"))
    job.callback_received_at = now_datetime()
    job.completed_at = now_datetime()
    job.next_retry_at = None
    job.active_key = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_error = None
    job.last_error_code = None
    job.save(ignore_permissions=True)
    mark_outbox_callback(
        job_doctype="AOS Video Processing Job", job_name=job.name,
        callback_status="completed", success=True,
    )
    return job


def mark_video_job_failed(job_id: str, error: str):
    """Operational helper retained for task/test callers; follows retry policy."""
    job = frappe.get_doc("AOS Video Processing Job", job_id, for_update=True)
    short = frappe.get_doc("AOS Short", job.short, for_update=True)
    return _schedule_retry(job, short, error) or job


def recover_video_processing_jobs(*, limit: int = 100) -> int:
    """Activate due retry attempts; outbox reconciliation owns in-flight recovery."""
    now = now_datetime()
    rows = frappe.db.sql(
        """SELECT name FROM `tabAOS Video Processing Job`
             WHERE status='Retry Waiting'
               AND next_retry_at IS NOT NULL
               AND next_retry_at<=%s
             ORDER BY next_retry_at ASC LIMIT %s""",
        (now, max(1, min(int(limit), 500))),
        as_dict=True,
    )
    count = 0
    for row in rows:
        try:
            job = frappe.get_doc("AOS Video Processing Job", row.name, for_update=True)
            if str(job.status) != "Retry Waiting" or not job.next_retry_at or job.next_retry_at > now:
                continue
            lifecycle = frappe.db.get_value("AOS Short", job.short, "lifecycle_status")
            raw_exists = bool(job.raw_video_media and frappe.db.exists("AOS Media Object", job.raw_video_media))
            source_exists = not job.source_short or bool(frappe.db.exists("AOS Short", job.source_short))
            if not lifecycle or lifecycle == "Deleted":
                # Retry rows can legitimately outlive a deleted Short/Media during
                # cleanup. Do not call Document.save() here because Frappe would
                # revalidate already-orphaned Link fields and turn cleanup into a
                # recurring scheduler error. This is an operational terminal-state
                # update, not a new domain mutation.
                frappe.db.sql(
                    """UPDATE `tabAOS Video Processing Job`
                          SET status='Cancelled', active_key=NULL, next_retry_at=NULL,
                              lease_owner=NULL, lease_expires_at=NULL, completed_at=%s,
                              last_error_code='SOURCE_DELETED', last_error='SOURCE_DELETED'
                        WHERE name=%s AND status='Retry Waiting'""",
                    (now_datetime(), job.name),
                )
                continue
            if not raw_exists or not source_exists:
                frappe.db.sql(
                    """UPDATE `tabAOS Video Processing Job`
                          SET status='Failed', active_key=NULL, next_retry_at=NULL,
                              lease_owner=NULL, lease_expires_at=NULL, completed_at=%s,
                              last_error_code='SOURCE_UNAVAILABLE', last_error='SOURCE_UNAVAILABLE'
                        WHERE name=%s AND status='Retry Waiting'""",
                    (now_datetime(), job.name),
                )
                if str(job.operation) != "Download":
                    frappe.db.set_value(
                        "AOS Short", job.short,
                        {
                            "processing_status": PROCESSING_FAILED,
                            "lifecycle_status": LIFECYCLE_FAILED,
                            "processing_error": "Source media is no longer available.",
                        },
                        update_modified=True,
                    )
                continue
            job.status = "Queued"
            job.next_retry_at = None
            job.save(ignore_permissions=True)
            enqueue_video_processing_dispatch(job)
            count += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), f"Video processing retry activation failed: {row.name}")
    return count
