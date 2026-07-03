"""Frappe-side orchestration for external video processing."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

import frappe
import requests
from frappe.utils import now_datetime

from aos.services.media.media_service import MediaService
from aos.utils.aos_config import (
    clean_url,
    get_env,
    get_env_int,
    get_first_env,
    get_minio_config,
)


class VideoProcessingError(RuntimeError):
    """Raised when video processing orchestration fails."""


@dataclass(frozen=True)
class VideoProcessingConfig:
    service_url: str
    service_secret: str
    callback_secret: str
    callback_url: str
    request_timeout_seconds: int
    max_attempts: int
    queue: str
    dispatcher_timeout_seconds: int


def get_max_short_duration_seconds() -> int:
    """Return Shorts max duration without importing aos.api.shorts.

    Importing aos.api.shorts.constants executes aos.api.shorts.__init__, which imports
    upload.py. upload.py imports this module, causing a circular import inside workers
    and manual dispatch. Keep this value env-driven for the separated video service.
    """
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
        if domain:
            callback_url = f"https://{domain}/api/method/aos.api.video_processing.handle_callback"
        else:
            callback_url = "http://127.0.0.1:8000/api/method/aos.api.video_processing.handle_callback"

    return VideoProcessingConfig(
        service_url=service_url,
        service_secret=get_env("VIDEO_SERVICE_SECRET", "") or "",
        callback_secret=get_env("VIDEO_SERVICE_CALLBACK_SECRET", "") or "",
        callback_url=callback_url,
        request_timeout_seconds=get_env_int("VIDEO_SERVICE_REQUEST_TIMEOUT_SECONDS", 20, min_value=5, max_value=120),
        max_attempts=get_env_int("VIDEO_MAX_RETRIES", 3, min_value=1, max_value=10),
        queue=get_env("VIDEO_FRAPPE_QUEUE", "long") or "long",
        dispatcher_timeout_seconds=get_env_int("VIDEO_DISPATCHER_TIMEOUT_SECONDS", 300, min_value=60, max_value=1800),
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")


def build_signature(secret: str, payload: bytes) -> str:
    digest = hmac.new(str(secret or "").encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not secret:
        return True
    if not signature:
        return False
    return hmac.compare_digest(build_signature(secret, payload), str(signature).strip())


def create_video_processing_job(
    *,
    short_id: str,
    force: bool = False,
    reason: str = "short_upload",
    enqueue: bool = True,
) -> object:
    """Create a persistent job record and optionally enqueue dispatch."""
    short_id = str(short_id or "").strip()
    if not short_id:
        raise VideoProcessingError("Short id is required")

    short = frappe.get_doc("AOS Short", short_id)
    raw_media_id = str(getattr(short, "raw_video_media", "") or "").strip()
    if not raw_media_id:
        raise VideoProcessingError("Short raw video media is missing")

    media = MediaService().get_media_doc(raw_media_id)
    if media.purpose != "short_video_raw":
        raise VideoProcessingError("Short raw video media has the wrong purpose")
    if media.visibility != "Private":
        raise VideoProcessingError("Short raw video media must be private")
    if media.status != "Attached":
        raise VideoProcessingError("Short raw video media must be attached")

    is_ready_reprocess = bool(force and short.status == "ready")
    if is_ready_reprocess:
        if hasattr(short, "audio_mix_status"):
            short.audio_mix_status = "pending"
        if hasattr(short, "audio_mix_error"):
            short.audio_mix_error = None
    else:
        if short.status not in {"uploaded", "failed", "processing"}:
            raise VideoProcessingError(f"Short cannot be processed from status {short.status}")
        short.status = "processing"
        short.processing_error = None
        if hasattr(short, "audio_mix_status"):
            short.audio_mix_status = "processing"
        if hasattr(short, "audio_mix_error"):
            short.audio_mix_error = None
    short.save(ignore_permissions=True)

    config = get_video_processing_config()
    job = frappe.get_doc(
        {
            "doctype": "AOS Video Processing Job",
            "short": short.name,
            "raw_video_media": raw_media_id,
            "status": "Queued",
            "force_reprocess": 1 if force else 0,
            "reason": reason or "short_upload",
            "attempt_count": 0,
            "max_attempts": config.max_attempts,
            "idempotency_key": uuid.uuid4().hex,
        }
    )
    job.insert(ignore_permissions=True)
    frappe.db.commit()

    if enqueue:
        enqueue_dispatch(job.name)

    return job


def enqueue_dispatch(job_id: str) -> None:
    config = get_video_processing_config()
    frappe.enqueue(
        "aos.tasks.video_processing.dispatch_video_processing_job",
        job_id=job_id,
        queue=config.queue,
        timeout=config.dispatcher_timeout_seconds,
    )


def dispatch_video_processing_job(job_id: str) -> object:
    """Lightweight Frappe RQ dispatcher. Does not run FFmpeg."""
    job = frappe.get_doc("AOS Video Processing Job", job_id)
    if job.status in {"Ready", "Processing"} and getattr(job, "service_job_id", None):
        return job
    if job.status in {"Cancelled"}:
        return job

    config = get_video_processing_config()
    job.status = "Dispatching"
    job.attempt_count = int(job.attempt_count or 0) + 1
    job.last_error = None
    job.dispatched_at = now_datetime()
    job.save(ignore_permissions=True)
    frappe.db.commit()

    payload = build_video_job_payload(job)
    job.request_payload = json.dumps(payload, default=str)
    job.save(ignore_permissions=True)
    frappe.db.commit()

    body = _json_bytes(payload)
    headers = {
        "Content-Type": "application/json",
        "X-AOS-Signature": build_signature(config.service_secret, body),
    }

    try:
        response = requests.post(
            f"{config.service_url}/jobs",
            data=body,
            headers=headers,
            timeout=config.request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}

        job.reload()
        job.status = "Processing"
        job.service_job_id = str(data.get("service_job_id") or data.get("job_id") or "")
        job.dispatched_at = now_datetime()
        job.save(ignore_permissions=True)
        frappe.db.commit()
        return job
    except Exception as exc:
        error = str(exc) or "Failed to dispatch video job"
        mark_video_job_failed(job.name, error, dispatch_failure=True)
        raise


def build_video_job_payload(job) -> dict[str, Any]:
    short = frappe.get_doc("AOS Short", job.short)
    raw = MediaService().get_media_doc(job.raw_video_media)
    minio = get_minio_config()
    sound = _build_sound_payload(short.name)

    return {
        "job_id": job.name,
        "short_id": short.name,
        "force": bool(int(getattr(job, "force_reprocess", 0) or 0)),
        "callback_url": get_video_processing_config().callback_url,
        "raw_video": {
            "media_id": raw.name,
            "bucket": raw.bucket,
            "object_key": raw.object_key,
            "content_type": raw.content_type,
            "size_bytes": int(raw.size_bytes or 0),
            "filename": raw.original_filename,
        },
        "sound": sound,
        "output": {
            "legacy_output_bucket": minio.bucket,
            "legacy_output_base_path": f"{minio.base_path}/processed".strip("/"),
            "thumbnail_bucket": minio.public_bucket,
            "thumbnail_base_path": "shorts/thumbnails",
            "max_duration_seconds": get_max_short_duration_seconds(),
        },
    }


def _build_sound_payload(short_id: str) -> dict[str, Any] | None:
    rows = frappe.db.sql(
        """
        SELECT
            ss.sound,
            ss.start_ms,
            ss.duration_ms,
            ss.volume,
            ss.is_original_audio,
            snd.sound_media,
            snd.status
        FROM `tabAOS Short Sound` ss
        INNER JOIN `tabAOS Sound` snd ON snd.name = ss.sound
        WHERE ss.short = %s
        LIMIT 1
        """,
        (short_id,),
        as_dict=True,
    )
    if not rows:
        return None

    row = rows[0]
    if row.get("status") != "active":
        raise VideoProcessingError("Selected sound is not active")
    if int(row.get("is_original_audio") or 0):
        return None
    if not row.get("sound_media"):
        raise VideoProcessingError("Selected sound media is missing")

    media = MediaService().get_media_doc(row.sound_media)
    return {
        "sound_id": row.sound,
        "media_id": media.name,
        "bucket": media.bucket,
        "object_key": media.object_key,
        "content_type": media.content_type,
        "filename": media.original_filename,
        "start_ms": int(row.get("start_ms") or 0),
        "duration_ms": int(row.get("duration_ms") or 0),
        "volume": float(row.get("volume") if row.get("volume") is not None else 1.0),
    }


def handle_video_processing_callback(payload: dict[str, Any]) -> object:
    job_id = str(payload.get("job_id") or "").strip()
    if not job_id:
        raise VideoProcessingError("job_id is required")
    if not frappe.db.exists("AOS Video Processing Job", job_id):
        raise VideoProcessingError("Video processing job not found")

    job = frappe.get_doc("AOS Video Processing Job", job_id)
    incoming_status = str(payload.get("status") or "").strip().lower()

    # Idempotent success callback.
    if job.status == "Ready" and incoming_status == "ready":
        return job

    if incoming_status == "ready":
        return mark_video_job_ready(job, payload)
    if incoming_status == "failed":
        return mark_video_job_failed(job.name, str(payload.get("error") or "Video processing failed"))

    raise VideoProcessingError("Invalid video callback status")


def mark_video_job_ready(job, payload: dict[str, Any]) -> object:
    short = frappe.get_doc("AOS Short", job.short)
    force = bool(int(getattr(job, "force_reprocess", 0) or 0))

    thumbnail_media_id = None
    thumbnail = payload.get("thumbnail") if isinstance(payload.get("thumbnail"), dict) else None
    if thumbnail and thumbnail.get("bucket") and thumbnail.get("object_key"):
        thumbnail_media_id = _create_thumbnail_media_from_existing_object(short, thumbnail)

    short.reload()
    short.playback_url = str(payload.get("playback_url") or "")
    short.processed_file_url = str(payload.get("processed_file_url") or "")
    short.processed_file_key = str(payload.get("processed_file_key") or "")
    short.duration_seconds = float(payload.get("duration_seconds") or 0)
    if thumbnail_media_id and short.meta.has_field("thumbnail_media"):
        short.thumbnail_media = thumbnail_media_id
    if thumbnail and thumbnail.get("url"):
        short.thumbnail_url = str(thumbnail.get("url") or "")

    short.status = "ready"
    short.processing_error = None
    if hasattr(short, "audio_mix_status"):
        short.audio_mix_status = "ready" if payload.get("sound_applied") else "none"
    if hasattr(short, "audio_mix_error"):
        short.audio_mix_error = None
    short.save(ignore_permissions=True)

    job.status = "Ready"
    job.callback_received_at = now_datetime()
    job.completed_at = now_datetime()
    job.last_error = None
    job.playback_url = short.playback_url
    job.processed_file_url = short.processed_file_url
    job.processed_file_key = short.processed_file_key
    job.thumbnail_media = thumbnail_media_id or getattr(short, "thumbnail_media", None)
    job.duration_seconds = short.duration_seconds
    job.response_payload = json.dumps(payload, default=str)
    job.save(ignore_permissions=True)
    frappe.db.commit()
    return job


def _create_thumbnail_media_from_existing_object(short, thumbnail: dict[str, Any]) -> str | None:
    service = MediaService()
    doc = service.create_uploaded_from_existing_object(
        user=short.owner,
        purpose="short_thumbnail",
        filename=str(thumbnail.get("filename") or f"{short.name}_thumbnail.jpg"),
        content_type=str(thumbnail.get("content_type") or "image/jpeg"),
        bucket=str(thumbnail.get("bucket") or ""),
        object_key=str(thumbnail.get("object_key") or ""),
        size_bytes=int(thumbnail.get("size_bytes") or 0),
        etag=str(thumbnail.get("etag") or ""),
        width=int(thumbnail.get("width") or 0) or None,
        height=int(thumbnail.get("height") or 0) or None,
    )
    service.attach_media(
        media_id=doc.name,
        user=short.owner,
        purpose="short_thumbnail",
        attached_doctype="AOS Short",
        attached_name=short.name,
        attached_field="thumbnail_media",
    )
    return doc.name


def mark_video_job_failed(job_id: str, error: str, *, dispatch_failure: bool = False) -> object:
    job = frappe.get_doc("AOS Video Processing Job", job_id)
    short = frappe.get_doc("AOS Short", job.short)
    force = bool(int(getattr(job, "force_reprocess", 0) or 0))
    error_text = str(error or "Video processing failed")[:1000]

    if force and short.status == "ready":
        if hasattr(short, "audio_mix_status"):
            short.audio_mix_status = "failed"
        if hasattr(short, "audio_mix_error"):
            short.audio_mix_error = error_text
    else:
        short.status = "failed"
        short.processing_error = error_text
        if hasattr(short, "audio_mix_status"):
            short.audio_mix_status = "failed"
        if hasattr(short, "audio_mix_error"):
            short.audio_mix_error = error_text
    short.save(ignore_permissions=True)

    job.status = "Failed"
    job.completed_at = now_datetime()
    job.last_error = error_text
    job.save(ignore_permissions=True)
    frappe.db.commit()
    return job
