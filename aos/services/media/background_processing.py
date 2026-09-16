"""Durable asynchronous processing owned by the Media domain."""

from __future__ import annotations

import hashlib
import io
import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.integrations.ai.background_removal_client import (
    BackgroundRemovalProcessingError,
    BackgroundRemovalUnavailableError,
    get_background_removal_client_settings,
    remove_background_from_file,
)
from aos.services.media.content_validation import (
    MediaContentValidationError,
    sniff_content_type,
    validate_image_bytes,
)
from aos.services.media.media_purposes import IMAGE_TYPES, get_media_purpose
from aos.services.media.media_service import (
    ACTIVE_READABLE_STATUSES,
    MediaConflictError,
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaStorageError,
    MediaValidationError,
    serialize_media_doc,
)
from aos.services.media.observability import media_log
from aos.utils.aos_config import get_env, get_env_int

BACKGROUND_REMOVAL_OUTPUT_PURPOSES = frozenset(
    {
        "ad_image",
        "review_image",
        "seller_banner",
        "live_cover",
        "profile_image",
        "background_removal_source",
    }
)
_OPERATION = "Background Removal"
_TERMINAL = {"Succeeded", "Failed"}


class MediaProcessingService:
    """Create, execute, retry, and query Media processing jobs."""

    def __init__(self, media_service: MediaService | None = None):
        self.media = media_service or MediaService()

    def request_background_removal(
        self,
        *,
        user: str,
        source_media_id: str,
        result_purpose: str | None = None,
    ):
        source = self.media.get_media_doc(source_media_id)
        self.media.assert_user_can_manage(source, user)
        self._validate_source(source)
        output = str(result_purpose or source.purpose or "").strip()
        self._validate_output_purpose(output)

        request_key = hashlib.sha256(
            f"{user}\n{source.name}\n{_OPERATION}\n{output}".encode("utf-8")
        ).hexdigest()
        existing = frappe.db.get_value(
            "AOS Media Processing Job", {"request_key": request_key}, "name"
        )
        max_attempts = get_env_int(
            "AOS_MEDIA_PROCESSING_MAX_ATTEMPTS", 3, min_value=1, max_value=10
        )
        if existing:
            job = frappe.get_doc("AOS Media Processing Job", existing, for_update=True)
            if job.status == "Failed":
                # A new owner request is an explicit retry of the same idempotent
                # operation. Reuse the durable row but reset terminal execution
                # state so a transient/code failure does not permanently poison
                # this source-media/result-purpose pair.
                job.status = "Queued"
                job.attempt_count = 0
                job.max_attempts = max_attempts
                job.started_at = None
                job.completed_at = None
                job.next_attempt_at = None
                job.result_media = None
                job.last_error_code = ""
                job.last_error_message = ""
                job.save(ignore_permissions=True)
                self.enqueue(job.name, after_commit=True)
            return job

        doc = frappe.get_doc(
            {
                "doctype": "AOS Media Processing Job",
                "owner_user": user,
                "source_media": source.name,
                "operation": _OPERATION,
                "result_purpose": output,
                "status": "Queued",
                "request_key": request_key,
                "attempt_count": 0,
                "max_attempts": max_attempts,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            # The unique request key is the cross-node idempotency boundary.
            existing = frappe.db.get_value(
                "AOS Media Processing Job", {"request_key": request_key}, "name"
            )
            if existing:
                return frappe.get_doc("AOS Media Processing Job", existing)
            raise exc
        self.enqueue(doc.name, after_commit=True)
        media_log(
            "processing_queued",
            media_id=source.name,
            purpose=source.purpose,
            operation="background_removal",
        )
        return doc

    def get_job(self, *, user: str, job_id: str):
        clean = str(job_id or "").strip()
        if not clean or not frappe.db.exists("AOS Media Processing Job", clean):
            raise MediaNotFoundError("Processing job not found", code="MEDIA_PROCESSING_NOT_FOUND")
        doc = frappe.get_doc("AOS Media Processing Job", clean)
        if doc.owner_user != user:
            raise MediaPermissionError("You cannot access this processing job", code="MEDIA_ACCESS_DENIED")
        return doc

    def enqueue(self, job_id: str, *, after_commit: bool) -> None:
        queue_name = str(get_env("AOS_MEDIA_PROCESSING_QUEUE_NAME", "long") or "long").strip() or "long"
        timeout = get_env_int(
            "AOS_MEDIA_PROCESSING_JOB_TIMEOUT_SECONDS", 240, min_value=60, max_value=1800
        )
        try:
            attempt = int(
                frappe.db.get_value("AOS Media Processing Job", job_id, "attempt_count") or 0
            ) + 1
            frappe.enqueue(
                "aos.tasks.media.process_media_processing_job",
                queue=queue_name,
                media_processing_job_id=job_id,
                job_id=f"aos-media-process:{job_id}:{attempt}",
                enqueue_after_commit=after_commit,
                timeout=timeout,
            )
        except Exception:
            # Queued/Retry Waiting DB state is durable and the 5-minute recovery
            # task is the delivery fallback if Redis/RQ is temporarily unavailable.
            media_log(
                "processing_enqueue_failed",
                operation="background_removal",
                outcome="retryable_failure",
                failure_category="queue_unavailable",
            )

    def process(self, *, job_id: str) -> str:
        job = self._claim(job_id)
        if job is None:
            current = frappe.db.get_value("AOS Media Processing Job", job_id, "status")
            return str(current or "missing")

        try:
            existing_result = frappe.db.get_value(
                "AOS Media Object", {"processing_job": job.name}, "name"
            )
            if existing_result:
                self._mark_succeeded(job.name, existing_result)
                return "Succeeded"

            source = self.media.get_media_doc(job.source_media)
            if source.owner_user != job.owner_user:
                raise MediaPermissionError("Source media ownership changed", code="MEDIA_ACCESS_DENIED")
            self._validate_source(source)
            self._validate_output_purpose(job.result_purpose)

            settings = get_background_removal_client_settings()
            source_bytes = self.media.storage.get_bytes(
                source.bucket,
                source.object_key,
                max_bytes=settings.max_image_bytes,
            )
            if not source_bytes:
                raise MediaValidationError("Source media is empty", code="INVALID_FILE")
            detected = str(sniff_content_type(source_bytes[: 64 * 1024]) or "").lower()
            if detected not in IMAGE_TYPES:
                raise MediaValidationError("Source media is not a supported image", code="INVALID_FILE")
            width, height = validate_image_bytes(source_bytes, expected_content_type=detected)
            if width * height > 16_000_000:
                raise MediaValidationError("Source image is too large for background removal", code="INVALID_FILE")

            stream = io.BytesIO(source_bytes)
            stream.name = str(source.original_filename or "image.png")
            result = remove_background_from_file(
                stream,
                filename=stream.name,
                content_type=detected,
            )
            result_width, result_height = validate_image_bytes(
                result.content,
                expected_content_type="image/png",
            )
            result_doc = self.media.create_uploaded_from_bytes(
                user=job.owner_user,
                purpose=job.result_purpose,
                filename=self._output_filename(source.original_filename),
                content_type="image/png",
                data=result.content,
                width=result_width or width,
                height=result_height or height,
                derived_from_media=source.name,
                processing_job=job.name,
            )
            # Make the result durable before updating the job. If the job update
            # is interrupted, a retry reconciles via Media.processing_job.
            try:
                frappe.db.commit()
            except Exception:
                try:
                    self.media.storage.delete_object(result_doc.bucket, result_doc.object_key)
                except Exception:
                    pass
                frappe.db.rollback()
                raise
            self._mark_succeeded(job.name, result_doc.name)
            media_log(
                "processing_completed",
                media_id=source.name,
                purpose=source.purpose,
                operation="background_removal",
            )
            return "Succeeded"
        except (MediaValidationError, MediaPermissionError, MediaNotFoundError, BackgroundRemovalProcessingError, MediaContentValidationError):
            self._record_failure(job.name, retryable=False, code="BACKGROUND_REMOVAL_FAILED")
            media_log(
                "processing_failed",
                media_id=job.source_media,
                operation="background_removal",
                outcome="failure",
                failure_category="invalid_or_unprocessable",
            )
            return "Failed"
        except (BackgroundRemovalUnavailableError, MediaStorageError):
            return self._record_retry(job.name, code="BACKGROUND_REMOVAL_UNAVAILABLE")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Media Processing Job Failed")
            return self._record_retry(job.name, code="MEDIA_PROCESSING_ERROR")

    def recover_due_jobs(self, *, limit: int = 100) -> int:
        if not frappe.db.exists("DocType", "AOS Media Processing Job"):
            return 0
        now = now_datetime()
        stale_before = add_to_date(
            now,
            seconds=-get_env_int(
                "AOS_MEDIA_PROCESSING_STALE_SECONDS", 600, min_value=120, max_value=7200
            ),
        )
        rows = frappe.get_all(
            "AOS Media Processing Job",
            filters={
                "status": ["in", ["Queued", "Retry Waiting", "Processing"]],
            },
            fields=["name", "status", "next_attempt_at", "started_at", "attempt_count", "max_attempts"],
            order_by="modified asc",
            limit=max(1, min(int(limit or 100), 500)),
        )
        queued = 0
        changed = 0
        terminal_failures: list[str] = []
        for row in rows:
            status = str(row.status or "")
            if status == "Processing":
                if not row.started_at or get_datetime(row.started_at) > get_datetime(stale_before):
                    continue
                frappe.db.set_value(
                    "AOS Media Processing Job",
                    row.name,
                    {"status": "Retry Waiting", "next_attempt_at": now},
                    update_modified=True,
                )
                changed += 1
            elif row.next_attempt_at and get_datetime(row.next_attempt_at) > get_datetime(now):
                continue
            if int(row.attempt_count or 0) >= int(row.max_attempts or 0):
                frappe.db.set_value(
                    "AOS Media Processing Job",
                    row.name,
                    {"status": "Failed", "completed_at": now, "next_attempt_at": None},
                    update_modified=True,
                )
                changed += 1
                terminal_failures.append(row.name)
                continue
            self.enqueue(row.name, after_commit=False)
            queued += 1
        if queued or changed:
            frappe.db.commit()
        for job_id in terminal_failures:
            self._notify_terminal_job(job_id=job_id, succeeded=False)
        return queued

    def _claim(self, job_id: str):
        if not frappe.db.exists("AOS Media Processing Job", job_id):
            return None
        job = frappe.get_doc("AOS Media Processing Job", job_id, for_update=True)
        if job.status in _TERMINAL:
            return None
        now = now_datetime()
        if job.status == "Processing" and job.started_at:
            stale_seconds = get_env_int(
                "AOS_MEDIA_PROCESSING_STALE_SECONDS", 600, min_value=120, max_value=7200
            )
            stale_before = add_to_date(now, seconds=-stale_seconds)
            if get_datetime(job.started_at) > get_datetime(stale_before):
                return None
        if job.next_attempt_at and get_datetime(job.next_attempt_at) > get_datetime(now):
            return None
        if int(job.attempt_count or 0) >= int(job.max_attempts or 0):
            job.status = "Failed"
            job.completed_at = now
            job.next_attempt_at = None
            job.save(ignore_permissions=True)
            frappe.db.commit()
            self._notify_terminal_job(job_id=job_id, succeeded=False)
            return None
        job.status = "Processing"
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.started_at = now
        job.next_attempt_at = None
        job.last_error_code = ""
        job.last_error_message = ""
        job.save(ignore_permissions=True)
        # Claim before external I/O so duplicate workers observe Processing.
        frappe.db.commit()
        return job

    def _mark_succeeded(self, job_id: str, result_media_id: str) -> None:
        job = frappe.get_doc("AOS Media Processing Job", job_id, for_update=True)
        job.status = "Succeeded"
        job.result_media = result_media_id
        job.completed_at = now_datetime()
        job.next_attempt_at = None
        job.last_error_code = ""
        job.last_error_message = ""
        job.save(ignore_permissions=True)
        frappe.db.commit()
        self._notify_terminal_job(job_id=job_id, succeeded=True)

    def _record_failure(self, job_id: str, *, retryable: bool, code: str) -> str:
        if retryable:
            return self._record_retry(job_id, code=code)
        job = frappe.get_doc("AOS Media Processing Job", job_id, for_update=True)
        job.status = "Failed"
        job.completed_at = now_datetime()
        job.next_attempt_at = None
        job.last_error_code = code
        job.last_error_message = "Media processing failed safely."
        job.save(ignore_permissions=True)
        frappe.db.commit()
        self._notify_terminal_job(job_id=job_id, succeeded=False)
        return "Failed"

    def _record_retry(self, job_id: str, *, code: str) -> str:
        job = frappe.get_doc("AOS Media Processing Job", job_id, for_update=True)
        attempts = int(job.attempt_count or 0)
        maximum = int(job.max_attempts or 0)
        if attempts >= maximum:
            job.status = "Failed"
            job.completed_at = now_datetime()
            job.next_attempt_at = None
            result = "Failed"
        else:
            backoff = min(300, 15 * (2 ** max(0, attempts - 1)))
            job.status = "Retry Waiting"
            job.next_attempt_at = add_to_date(now_datetime(), seconds=backoff)
            result = "Retry Waiting"
        job.last_error_code = code
        job.last_error_message = "Media processing dependency is temporarily unavailable."
        job.save(ignore_permissions=True)
        frappe.db.commit()
        if result == "Failed":
            self._notify_terminal_job(job_id=job_id, succeeded=False)
        media_log(
            "processing_retry_scheduled" if result == "Retry Waiting" else "processing_failed",
            media_id=job.source_media,
            operation="background_removal",
            outcome="retryable_failure" if result == "Retry Waiting" else "failure",
            failure_category=code,
            retry_count=attempts,
        )
        return result

    @staticmethod
    def _notify_terminal_job(*, job_id: str, succeeded: bool) -> None:
        """Request a user-visible Notification only after Media state committed."""
        try:
            job = frappe.db.get_value(
                "AOS Media Processing Job",
                job_id,
                ["owner_user", "source_media", "result_media", "status"],
                as_dict=True,
            )
            if not job or (succeeded and job.status != "Succeeded") or (not succeeded and job.status != "Failed"):
                return
            from aos.services.notifications.service import NotificationService

            if succeeded and job.result_media:
                NotificationService.notify_media_processing_completed(
                    user=job.owner_user,
                    job_id=job_id,
                    source_media_id=job.source_media,
                    result_media_id=job.result_media,
                )
            elif not succeeded:
                NotificationService.notify_media_processing_failed(
                    user=job.owner_user,
                    job_id=job_id,
                    source_media_id=job.source_media,
                )
            # This transaction contains Notification-owned rows only; the Media
            # terminal transition was committed before this helper was called.
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(frappe.get_traceback(), "AOS Media terminal notification failed")

    @staticmethod
    def _validate_source(source) -> None:
        if source.status == "Deleted":
            raise MediaNotFoundError("Media not found")
        if source.status not in ACTIVE_READABLE_STATUSES:
            raise MediaConflictError("Media is not ready", code="MEDIA_NOT_READY")
        content_type = str(source.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in IMAGE_TYPES:
            raise MediaValidationError("Only JPG, PNG, and WEBP images are supported", code="INVALID_FILE")

    @staticmethod
    def _validate_output_purpose(purpose: str) -> None:
        rule = get_media_purpose(purpose)
        if not rule or rule.key not in BACKGROUND_REMOVAL_OUTPUT_PURPOSES:
            raise MediaValidationError("Invalid result media purpose", code="INVALID_MEDIA_PURPOSE")
        if "image/png" not in rule.allowed_content_types:
            raise MediaValidationError("Result purpose must support PNG images", code="INVALID_MEDIA_PURPOSE")

    @staticmethod
    def _output_filename(original: str | None) -> str:
        raw = str(original or "image").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        stem = raw.rsplit(".", 1)[0][:80] or "image"
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in stem).strip("-") or "image"
        return f"{safe}-background-removed.png"


def serialize_processing_job(job, *, media_service: MediaService | None = None) -> dict[str, object]:
    media = None
    if job.status == "Succeeded" and job.result_media:
        service = media_service or MediaService()
        result = service.get_media_doc(job.result_media)
        url = service.get_url(media_id=result.name, user=job.owner_user)
        media = serialize_media_doc(result, url=url)
    return {
        "job_id": job.name,
        "operation": "background_removal",
        "status": job.status,
        "source_media_id": job.source_media,
        "result_purpose": job.result_purpose,
        "attempt_count": int(job.attempt_count or 0),
        "max_attempts": int(job.max_attempts or 0),
        "next_attempt_at": str(job.next_attempt_at) if job.next_attempt_at else None,
        "error": str(job.last_error_code or "") or None,
        "media": media,
    }
