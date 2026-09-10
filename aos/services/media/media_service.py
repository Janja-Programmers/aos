"""Production media application/domain service for AOS-owned object storage."""

from __future__ import annotations

import hashlib
import math
import os
import uuid
from datetime import datetime
from typing import Any, Iterable

import frappe
from frappe.query_builder.functions import Count
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.services.media.content_validation import (
    CHUNK_SIZE,
    MediaContentValidationError,
    extract_iso_bmff_duration_seconds,
    normalize_checksum,
    normalize_content_type,
    normalize_filename,
    sanitize_public_image_bytes,
    sha256_chunks,
    validate_filename_extension,
    validate_image_bytes,
    validate_magic_type,
)
from aos.services.media.media_purposes import MediaPurpose, get_media_purpose
from aos.services.media.observability import media_log
from aos.services.media.resource_authorization import (
    ResourceAuthorizationError,
    ResourceNotFoundError,
    assert_attachment_target_allowed,
    assert_purpose_upload_allowed,
)
from aos.services.storage.base import (
    MultipartPart,
    ObjectStat,
    StorageAdapter,
    StorageConfigurationError,
    StorageNotFoundError,
    StorageUnavailableError,
    StorageValidationError,
)
from aos.services.storage.s3_compatible import S3CompatibleStorage
from aos.utils.aos_config import get_env, get_env_int, get_media_download_expiry_minutes
from aos.utils.aos_settings import get_aos_settings_snapshot
from aos.utils.doctype_permissions import has_doctype_permission

ACTIVE_READABLE_STATUSES = {"Uploaded", "Processing", "Ready", "Attached"}
TERMINAL_STATUSES = {"Failed", "Replaced", "Deleted"}
MIN_MULTIPART_PART_SIZE_BYTES = 5 * 1024 * 1024
MAX_MULTIPART_PARTS = 10000
MAX_MULTIPART_PART_URL_BATCH = 20


class MediaError(Exception):
    """Base public-safe media exception with a stable machine code."""

    code = "MEDIA_ERROR"

    def __init__(self, message: str, *, code: str | None = None):
        super().__init__(message)
        self.code = str(code or self.code)


class MediaValidationError(MediaError, ValueError):
    code = "INVALID_FILE"


class MediaPermissionError(MediaError, PermissionError):
    code = "MEDIA_ACCESS_DENIED"


class MediaNotFoundError(MediaError, FileNotFoundError):
    code = "MEDIA_NOT_FOUND"


class MediaConflictError(MediaValidationError):
    code = "INVALID_STATE"


class MediaStorageError(MediaError, RuntimeError):
    code = "STORAGE_UNAVAILABLE"


class MediaService:
    """Own media authorization, lifecycle, storage identity, and serialization."""

    def __init__(self, storage: StorageAdapter | None = None):
        # Storage configuration is intentionally resolved lazily. MediaService is
        # instantiated from DocType hooks that also run during app installation,
        # fixture import, schema synchronization, and migrations. Those code paths
        # may only need database-level relationship validation (or may be no-ops)
        # and must not require object-storage credentials merely to construct the
        # domain service.
        self._storage = storage

    @property
    def storage(self) -> StorageAdapter:
        """Return the configured storage adapter when an operation needs it.

        Missing or invalid object-storage configuration is therefore reported at
        the actual storage boundary, while install and migrate hooks that do not
        perform storage I/O remain deterministic and side-effect free.
        """
        if self._storage is None:
            self._storage = S3CompatibleStorage()
        return self._storage

    # INIT / CONFIRM
    def init_upload(
        self,
        *,
        user: str,
        purpose: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        duration_seconds: float | None = None,
        checksum_sha256: str | None = None,
        idempotency_key: str | None = None,
        upload_mode: str | None = None,
        system: bool = False,
    ) -> tuple[object, str | None, dict[str, str], int]:
        policy = self._get_purpose_or_raise(purpose)
        self._assert_purpose_upload_allowed(user=user, policy=policy, system=system)

        clean_filename = self._normalize_filename(filename)
        clean_content_type = self._normalize_content_type(content_type, clean_filename)
        clean_size = self._normalize_size(size_bytes)
        clean_duration = self._validate_duration(policy, duration_seconds)
        if policy.processing_required and policy.max_duration_seconds and clean_duration is None:
            raise MediaValidationError(
                "Media duration is required before upload",
                code="DURATION_REQUIRED",
            )
        clean_checksum = self._normalize_checksum(checksum_sha256)
        validate_filename_extension(clean_filename, policy.allowed_extensions)
        self._validate_upload_request(
            purpose_rule=policy,
            content_type=clean_content_type,
            size_bytes=clean_size,
        )
        selected_upload_mode = self._select_upload_mode(
            policy=policy,
            size_bytes=clean_size,
            requested_mode=upload_mode,
        )
        multipart_part_size = 0
        multipart_part_count = 0
        if selected_upload_mode == "multipart":
            multipart_part_size = self._multipart_part_size(policy)
            multipart_part_count = self._multipart_part_count(
                size_bytes=clean_size,
                part_size_bytes=multipart_part_size,
            )

        idempotency_hash = self._hash_idempotency_key(user, policy.key, idempotency_key)
        if idempotency_hash:
            existing = self._find_reusable_initiated_upload(
                user=user,
                purpose=policy.key,
                idempotency_hash=idempotency_hash,
            )
            if existing:
                self._assert_idempotent_upload_matches(
                    existing,
                    filename=clean_filename,
                    content_type=clean_content_type,
                    size_bytes=clean_size,
                    duration_seconds=clean_duration,
                    checksum_sha256=clean_checksum,
                    upload_mode=selected_upload_mode,
                )
                return self._issue_upload_contract(existing)

        try:
            final_bucket = self.storage.bucket_for_type(policy.bucket_type)
            upload_directly_to_final = bool(
                selected_upload_mode == "multipart"
                and policy.is_private
                and getattr(policy, "multipart_upload_to_final", False)
            )
            staging_bucket = (
                final_bucket
                if upload_directly_to_final
                else self.storage.bucket_for_type("private")
            )
            # A private Short's multipart upload and final object intentionally
            # share one bucket. Avoid duplicate bucket-existence probes on every
            # initialization request; those control-plane calls become visible at
            # high aggregate upload rates.
            self.storage.ensure_bucket(staging_bucket, public_read=False)
            if final_bucket != staging_bucket:
                self.storage.ensure_bucket(final_bucket, public_read=policy.is_public)
            elif policy.is_public:
                self.storage.ensure_bucket(final_bucket, public_read=True)
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        if idempotency_hash or selected_upload_mode == "multipart":
            # The initial lookup above is only a fast path. Serialize the final
            # check-and-insert boundary across workers. This prevents both
            # duplicate idempotent uploads and concurrent multipart requests
            # from racing past the per-user active-session quota. Locking the
            # owner User row avoids a new global lock service and only
            # serializes upload initialization for the same account.
            frappe.db.sql(
                "SELECT name FROM `tabUser` WHERE name = %s FOR UPDATE",
                (user,),
            )
            if idempotency_hash:
                existing = self._find_reusable_initiated_upload(
                    user=user,
                    purpose=policy.key,
                    idempotency_hash=idempotency_hash,
                )
                if existing:
                    self._assert_idempotent_upload_matches(
                        existing,
                        filename=clean_filename,
                        content_type=clean_content_type,
                        size_bytes=clean_size,
                        duration_seconds=clean_duration,
                        checksum_sha256=clean_checksum,
                        upload_mode=selected_upload_mode,
                    )
                    return self._issue_upload_contract(existing)
            if selected_upload_mode == "multipart":
                self._enforce_active_multipart_limit(user=user)

        final_key = self.generate_object_key(
            owner_user=user,
            purpose_rule=policy,
            filename=clean_filename,
        )
        staging_key = (
            final_key
            if upload_directly_to_final
            else self.generate_staging_object_key(
                owner_user=user,
                purpose_rule=policy,
                filename=clean_filename,
            )
        )
        expires_at = add_to_date(
            now_datetime(),
            minutes=self.get_upload_session_expiry_minutes(
                policy,
                upload_mode=selected_upload_mode,
            ),
        )

        doc = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": user,
                "bucket": final_bucket,
                "object_key": final_key,
                "upload_bucket": staging_bucket,
                "upload_object_key": staging_key,
                "upload_mode": selected_upload_mode,
                "multipart_part_size_bytes": multipart_part_size,
                "multipart_part_count": multipart_part_count,
                "multipart_last_activity_at": (
                    now_datetime() if selected_upload_mode == "multipart" else None
                ),
                "original_filename": clean_filename,
                "content_type": clean_content_type,
                "expected_size_bytes": clean_size,
                "size_bytes": clean_size,
                "expected_checksum": clean_checksum,
                "visibility": policy.visibility,
                "purpose": policy.key,
                "status": "Initialized",
                "upload_expires_at": expires_at,
                "idempotency_key_hash": idempotency_hash,
                "duration_seconds": clean_duration,
                "retry_count": 0,
            }
        )
        doc.insert(ignore_permissions=True)

        try:
            result = self._issue_upload_contract(doc)
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            self._mark_failed(
                doc,
                code="STORAGE_UNAVAILABLE",
                reason="Upload URL generation failed",
            )
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        media_log(
            "upload_initiated",
            media_id=doc.name,
            purpose=policy.key,
            operation="init",
            bytes_count=clean_size,
        )
        return result

    def _issue_upload_contract(
        self,
        doc,
    ) -> tuple[object, str | None, dict[str, str], int]:
        mode = str(getattr(doc, "upload_mode", "") or "direct").strip().lower()
        if mode != "multipart":
            return self._issue_upload_url(doc)

        policy = self._get_purpose_or_raise(doc.purpose)
        expires_in = self._remaining_upload_session_seconds(doc)
        if expires_in <= 0:
            raise MediaValidationError("Upload has expired", code="UPLOAD_EXPIRED")
        upload_bucket, upload_key = self._upload_identity(doc)
        upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
        if not upload_id:
            try:
                upload_id = self.storage.create_multipart_upload(
                    upload_bucket,
                    upload_key,
                    content_type=normalize_content_type(doc.content_type),
                )
                doc.multipart_upload_id = upload_id
                doc.multipart_last_activity_at = now_datetime()
                doc.save(ignore_permissions=True)
            except (
                StorageConfigurationError,
                StorageUnavailableError,
                StorageValidationError,
            ):
                if upload_id:
                    try:
                        self.storage.abort_multipart_upload(
                            upload_bucket,
                            upload_key,
                            upload_id=upload_id,
                        )
                    except Exception:
                        pass
                raise
            except Exception:
                if upload_id:
                    try:
                        self.storage.abort_multipart_upload(
                            upload_bucket,
                            upload_key,
                            upload_id=upload_id,
                        )
                    except Exception:
                        pass
                raise

        expires_in = self._remaining_upload_session_seconds(doc)
        return doc, None, {}, expires_in

    def _issue_upload_url(self, doc) -> tuple[object, str, dict[str, str], int]:
        policy = self._get_purpose_or_raise(doc.purpose)
        remaining_seconds = self._remaining_upload_session_seconds(doc)
        if remaining_seconds <= 0:
            raise MediaValidationError("Upload has expired", code="UPLOAD_EXPIRED")
        configured_minutes = self.get_upload_expiry_minutes(policy)
        expiry_minutes = min(
            configured_minutes,
            max(1, int(math.ceil(remaining_seconds / 60))),
        )
        upload_bucket = str(getattr(doc, "upload_bucket", "") or doc.bucket)
        upload_key = str(getattr(doc, "upload_object_key", "") or doc.object_key)
        upload_url = self.storage.presigned_put_url(
            upload_bucket,
            upload_key,
            expiry_minutes=expiry_minutes,
        )
        return (
            doc,
            upload_url,
            {"Content-Type": normalize_content_type(doc.content_type)},
            min(remaining_seconds, expiry_minutes * 60),
        )

    def get_multipart_part_urls(
        self,
        *,
        user: str,
        media_id: str,
        start_part: int | None = None,
        count: int | None = None,
    ) -> dict[str, object]:
        """Return a bounded batch of direct-to-storage UploadPart URLs.

        The storage upload id is deliberately never exposed as a first-class API
        field. It exists only inside the signed URLs and the server-side media
        record. Clients use ``media_id`` as the durable resumable session id.
        """
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        self._assert_multipart_mode(doc)
        self._assert_multipart_session_active(doc)

        total_parts = int(getattr(doc, "multipart_part_count", 0) or 0)
        safe_start, safe_count = self._normalize_part_url_window(
            total_parts=total_parts,
            start_part=start_part,
            count=count,
        )
        upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
        if not upload_id:
            raise MediaConflictError(
                "Multipart upload session is unavailable",
                code="MULTIPART_SESSION_LOST",
            )

        policy = self._get_purpose_or_raise(doc.purpose)
        remaining_seconds = self._remaining_upload_session_seconds(doc)
        if remaining_seconds <= 0:
            raise MediaValidationError("Upload has expired", code="UPLOAD_EXPIRED")
        configured_minutes = self.get_upload_expiry_minutes(policy)
        expiry_minutes = min(
            configured_minutes,
            max(1, int(math.ceil(remaining_seconds / 60))),
        )
        upload_bucket, upload_key = self._upload_identity(doc)
        urls: list[dict[str, object]] = []
        try:
            for part_number in range(safe_start, safe_start + safe_count):
                urls.append(
                    {
                        "part_number": part_number,
                        "expected_size_bytes": self._expected_multipart_part_size(
                            doc,
                            part_number,
                        ),
                        "upload_url": self.storage.presigned_upload_part_url(
                            upload_bucket,
                            upload_key,
                            upload_id=upload_id,
                            part_number=part_number,
                            expiry_minutes=expiry_minutes,
                        ),
                    }
                )
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            self._record_storage_failure(doc, exc)
            if isinstance(exc, StorageValidationError):
                raise MediaValidationError(
                    "Multipart upload request is invalid",
                    code="VALIDATION_ERROR",
                ) from exc
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        now = now_datetime()
        frappe.db.set_value(
            "AOS Media Object",
            doc.name,
            "multipart_last_activity_at",
            now,
            update_modified=False,
        )
        doc.multipart_last_activity_at = now
        return {
            "media_id": doc.name,
            "upload_mode": "multipart",
            "part_size_bytes": int(doc.multipart_part_size_bytes or 0),
            "part_count": total_parts,
            "part_url_expires_in": min(remaining_seconds, expiry_minutes * 60),
            "session_expires_in": self._remaining_upload_session_seconds(doc),
            "max_parallel_parts": self.get_multipart_max_parallel_parts(),
            "parts": urls,
        }

    def get_multipart_status(self, *, user: str, media_id: str) -> dict[str, object]:
        """Return authoritative resume state from object storage."""
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        self._assert_multipart_mode(doc)

        if doc.status in ACTIVE_READABLE_STATUSES:
            return self._multipart_status_payload(
                doc,
                uploaded_parts=list(range(1, int(doc.multipart_part_count or 0) + 1)),
                bytes_uploaded=int(doc.expected_size_bytes or doc.size_bytes or 0),
                storage_complete=True,
            )
        if doc.status in {"Failed", "Deleted", "Delete Pending", "Replaced", "Orphaned"}:
            return self._multipart_status_payload(
                doc,
                uploaded_parts=[],
                bytes_uploaded=0,
                storage_complete=False,
            )
        if doc.status != "Initialized":
            raise MediaConflictError(
                "Multipart upload is not resumable in its current state",
                code="INVALID_STATE",
            )

        upload_bucket, upload_key = self._upload_identity(doc)
        try:
            # If CompleteMultipartUpload succeeded but the request died before
            # Frappe persisted its terminal metadata, the staging object itself
            # is the durable source of truth. The completion endpoint can heal it.
            if self.storage.object_exists(upload_bucket, upload_key):
                stat = self.storage.stat_object(upload_bucket, upload_key)
                return self._multipart_status_payload(
                    doc,
                    uploaded_parts=list(range(1, int(doc.multipart_part_count or 0) + 1)),
                    bytes_uploaded=int(stat.size or 0),
                    storage_complete=True,
                )
        except (StorageConfigurationError, StorageUnavailableError) as exc:
            self._record_storage_failure(doc, exc)
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        if self._upload_is_expired(doc):
            self._abort_multipart_storage_best_effort(doc)
            self._mark_failed(
                doc,
                code="UPLOAD_EXPIRED",
                reason="Multipart upload completion window expired",
            )
            return self._multipart_status_payload(
                doc,
                uploaded_parts=[],
                bytes_uploaded=0,
                storage_complete=False,
            )

        upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
        if not upload_id:
            self._mark_failed(
                doc,
                code="MULTIPART_SESSION_LOST",
                reason="Multipart upload session id is missing",
            )
            return self._multipart_status_payload(
                doc,
                uploaded_parts=[],
                bytes_uploaded=0,
                storage_complete=False,
            )

        try:
            parts = self.storage.list_multipart_parts(
                upload_bucket,
                upload_key,
                upload_id=upload_id,
            )
        except StorageNotFoundError:
            self._mark_failed(
                doc,
                code="MULTIPART_SESSION_LOST",
                reason="Multipart upload session no longer exists in storage",
            )
            return self._multipart_status_payload(
                doc,
                uploaded_parts=[],
                bytes_uploaded=0,
                storage_complete=False,
            )
        except (StorageConfigurationError, StorageUnavailableError) as exc:
            self._record_storage_failure(doc, exc)
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        uploaded_numbers = [int(part.part_number) for part in parts]
        bytes_uploaded = sum(max(0, int(part.size or 0)) for part in parts)
        invalid_parts = [
            int(part.part_number)
            for part in parts
            if int(part.size or 0)
            != self._expected_multipart_part_size(doc, int(part.part_number))
        ]
        return self._multipart_status_payload(
            doc,
            uploaded_parts=uploaded_numbers,
            bytes_uploaded=bytes_uploaded,
            storage_complete=False,
            invalid_parts=invalid_parts,
        )

    def complete_multipart_upload(self, *, user: str, media_id: str) -> object:
        """Atomically close the storage multipart session and confirm media.

        No part ETags are trusted from the client. The server lists authoritative
        parts from object storage, validates the exact expected shape, completes
        the upload, then runs the normal media confirmation/magic-byte pipeline.
        """
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        self._assert_multipart_mode(doc)

        if doc.status in ACTIVE_READABLE_STATUSES:
            return doc
        if doc.status == "Deleted":
            raise MediaNotFoundError("Media not found", code="MEDIA_NOT_FOUND")
        if doc.status in {"Failed", "Replaced", "Delete Pending", "Orphaned"}:
            raise MediaConflictError(
                "Multipart upload cannot be completed in its current state",
                code="INVALID_STATE",
            )
        if doc.status != "Initialized":
            raise MediaConflictError("Multipart upload cannot be completed", code="INVALID_STATE")

        upload_bucket, upload_key = self._upload_identity(doc)
        storage_complete = False
        try:
            storage_complete = self.storage.object_exists(upload_bucket, upload_key)
        except (StorageConfigurationError, StorageUnavailableError) as exc:
            self._record_storage_failure(doc, exc)
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        if not storage_complete:
            if self._upload_is_expired(doc):
                self._abort_multipart_storage_best_effort(doc)
                self._mark_failed(
                    doc,
                    code="UPLOAD_EXPIRED",
                    reason="Multipart upload completion window expired",
                )
                raise MediaValidationError("Upload has expired", code="UPLOAD_EXPIRED")

            upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
            if not upload_id:
                self._mark_failed(
                    doc,
                    code="MULTIPART_SESSION_LOST",
                    reason="Multipart upload session id is missing",
                )
                raise MediaConflictError(
                    "Multipart upload session is unavailable",
                    code="MULTIPART_SESSION_LOST",
                )

            try:
                parts = self.storage.list_multipart_parts(
                    upload_bucket,
                    upload_key,
                    upload_id=upload_id,
                )
            except StorageNotFoundError as exc:
                self._mark_failed(
                    doc,
                    code="MULTIPART_SESSION_LOST",
                    reason="Multipart upload session no longer exists in storage",
                )
                raise MediaConflictError(
                    "Multipart upload session is unavailable",
                    code="MULTIPART_SESSION_LOST",
                ) from exc
            except (StorageConfigurationError, StorageUnavailableError) as exc:
                self._record_storage_failure(doc, exc)
                raise MediaStorageError("Storage is temporarily unavailable") from exc

            self._validate_complete_multipart_parts(doc=doc, parts=parts)
            try:
                completed_stat = self.storage.complete_multipart_upload(
                    upload_bucket,
                    upload_key,
                    upload_id=upload_id,
                    parts=parts,
                )
            except StorageNotFoundError as exc:
                # An ambiguous CompleteMultipartUpload response can arrive as
                # NoSuchUpload after storage already assembled the object. The
                # staging object is durable truth; heal when it exists and has
                # the declared size, otherwise treat the session as lost.
                try:
                    completed_stat = self.storage.stat_object(upload_bucket, upload_key)
                except StorageNotFoundError:
                    self._mark_failed(
                        doc,
                        code="MULTIPART_SESSION_LOST",
                        reason="Multipart upload session disappeared before completion could be reconciled",
                    )
                    raise MediaConflictError(
                        "Multipart upload session is unavailable",
                        code="MULTIPART_SESSION_LOST",
                    ) from exc
                except (StorageConfigurationError, StorageUnavailableError) as stat_exc:
                    self._record_storage_failure(doc, stat_exc)
                    raise MediaStorageError("Storage is temporarily unavailable") from stat_exc
            except StorageValidationError as exc:
                raise MediaValidationError(
                    "Multipart upload parts are invalid",
                    code="MULTIPART_INCOMPLETE",
                ) from exc
            except (StorageConfigurationError, StorageUnavailableError) as exc:
                self._record_storage_failure(doc, exc)
                raise MediaStorageError("Storage is temporarily unavailable") from exc

            if int(completed_stat.size or 0) != int(doc.expected_size_bytes or 0):
                self._delete_staging_best_effort(doc)
                self._mark_failed(
                    doc,
                    code="SIZE_MISMATCH",
                    reason="Completed multipart upload size does not match the declared file size",
                )
                raise MediaValidationError(
                    "Uploaded file size does not match",
                    code="SIZE_MISMATCH",
                )

        now = now_datetime()
        doc.multipart_completed_at = doc.multipart_completed_at or now
        doc.multipart_last_activity_at = now
        doc.save(ignore_permissions=True)
        media_log(
            "multipart_upload_completed",
            media_id=doc.name,
            purpose=doc.purpose,
            operation="multipart_complete",
            bytes_count=int(doc.expected_size_bytes or 0),
        )
        return self.confirm_upload(user=user, media_id=doc.name)

    def abort_multipart_upload(self, *, user: str, media_id: str) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        self._assert_multipart_mode(doc)

        if doc.status in ACTIVE_READABLE_STATUSES:
            return doc
        if doc.status == "Failed" and str(getattr(doc, "failure_code", "") or "") == "UPLOAD_ABORTED":
            return doc
        if doc.status != "Initialized":
            raise MediaConflictError(
                "Multipart upload cannot be aborted in its current state",
                code="INVALID_STATE",
            )

        upload_bucket, upload_key = self._upload_identity(doc)
        upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
        if upload_id:
            try:
                self.storage.abort_multipart_upload(
                    upload_bucket,
                    upload_key,
                    upload_id=upload_id,
                )
            except (StorageConfigurationError, StorageUnavailableError) as exc:
                self._record_storage_failure(doc, exc)
                raise MediaStorageError("Storage is temporarily unavailable") from exc

        # A completion that raced with a client cancellation is still private
        # staging data. Delete it best-effort and leave the normal cleanup marker
        # as a second line of defense.
        self._delete_staging_best_effort(doc)
        doc.status = "Failed"
        doc.failure_code = "UPLOAD_ABORTED"
        doc.failure_reason = "Upload was cancelled by the client"
        doc.failed_at = now_datetime()
        doc.multipart_aborted_at = now_datetime()
        doc.multipart_last_activity_at = now_datetime()
        doc.save(ignore_permissions=True)
        media_log(
            "multipart_upload_aborted",
            media_id=doc.name,
            purpose=doc.purpose,
            operation="multipart_abort",
            outcome="cancelled",
        )
        return doc

    def confirm_upload(self, *, user: str, media_id: str) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)

        if doc.status in ACTIVE_READABLE_STATUSES:
            return doc
        if doc.status == "Deleted":
            raise MediaNotFoundError("Media not found", code="MEDIA_NOT_FOUND")
        if doc.status in {"Failed", "Replaced", "Delete Pending", "Orphaned"}:
            raise MediaConflictError(
                "Media cannot be completed in its current state",
                code="INVALID_STATE",
            )
        if doc.status != "Initialized":
            raise MediaConflictError("Media cannot be completed", code="INVALID_STATE")
        if self._upload_is_expired(doc):
            self._delete_staging_best_effort(doc)
            self._mark_failed(doc, code="UPLOAD_EXPIRED", reason="Upload completion window expired")
            raise MediaValidationError("Upload has expired", code="UPLOAD_EXPIRED")

        policy = self._get_purpose_or_raise(doc.purpose)
        upload_bucket, upload_key = self._upload_identity(doc)

        try:
            stat = self.storage.stat_object(upload_bucket, upload_key)
        except StorageNotFoundError as exc:
            raise MediaNotFoundError("Upload is incomplete", code="UPLOAD_INCOMPLETE") from exc
        except (StorageUnavailableError, StorageConfigurationError) as exc:
            self._record_storage_failure(doc, exc)
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        try:
            validated = self._validate_confirmed_object(
                doc=doc,
                purpose_rule=policy,
                stat=stat,
                bucket=upload_bucket,
                object_key=upload_key,
            )
        except (MediaValidationError, MediaContentValidationError) as exc:
            self._delete_staging_best_effort(doc)
            code = getattr(exc, "code", "INVALID_FILE")
            self._mark_failed(doc, code=code, reason=str(exc))
            media_log(
                "upload_rejected",
                media_id=doc.name,
                purpose=policy.key,
                operation="confirm",
                outcome="rejected",
                failure_category=code,
            )
            if isinstance(exc, MediaValidationError):
                raise
            raise MediaValidationError(str(exc), code=code) from exc

        final_stat = self._finalize_staged_object(
            doc=doc,
            source_bucket=upload_bucket,
            source_object_key=upload_key,
            expected_size=validated.size_bytes,
            finalized_bytes=validated.finalized_bytes,
            content_type=validated.content_type,
        )

        previous_values = {
            "status": doc.status,
            "size_bytes": doc.size_bytes,
            "content_type": doc.content_type,
            "checksum": getattr(doc, "checksum", ""),
        }
        try:
            doc.status = "Uploaded"
            doc.size_bytes = int(validated.size_bytes)
            doc.content_type = validated.content_type
            doc.checksum = validated.checksum_sha256
            doc.etag = final_stat.etag or stat.etag or ""
            doc.width = validated.width
            doc.height = validated.height
            if validated.duration_seconds is not None:
                doc.duration_seconds = validated.duration_seconds
            doc.uploaded_at = now_datetime()
            doc.completed_at = now_datetime()
            doc.failure_code = ""
            doc.failure_reason = ""
            doc.last_storage_error = ""
            doc.save(ignore_permissions=True)
        except Exception:
            try:
                self.storage.delete_object(doc.bucket, doc.object_key)
            except Exception:
                pass
            for key, value in previous_values.items():
                setattr(doc, key, value)
            raise

        self._cleanup_staging_after_finalize(doc)
        media_log(
            "upload_completed",
            media_id=doc.name,
            purpose=policy.key,
            operation="confirm",
            bytes_count=validated.size_bytes,
        )
        return doc

    # INTERNAL / SERVER-SIDE CREATION
    def create_uploaded_from_bytes(
        self,
        *,
        user: str,
        purpose: str,
        filename: str,
        content_type: str,
        data: bytes,
        width: int | None = None,
        height: int | None = None,
        duration_seconds: float | None = None,
        derived_from_media: str | None = None,
        processing_job: str | None = None,
    ) -> object:
        policy = self._get_purpose_or_raise(purpose)
        clean_duration = self._validate_duration(policy, duration_seconds)
        clean_filename = self._normalize_filename(filename)
        clean_content_type = self._normalize_content_type(content_type, clean_filename)
        payload = bytes(data or b"")
        validate_filename_extension(clean_filename, policy.allowed_extensions)
        self._validate_upload_request(
            purpose_rule=policy,
            content_type=clean_content_type,
            size_bytes=len(payload),
        )
        detected = validate_magic_type(
            head=payload[: 64 * 1024],
            claimed_content_type=clean_content_type,
            allowed_content_types=policy.allowed_content_types,
        )
        checksum, verified_size = sha256_chunks([payload], content_type=detected)
        if verified_size != len(payload):
            raise MediaValidationError("Media size verification failed", code="INVALID_FILE")
        actual_width = width
        actual_height = height
        if detected.startswith("image/"):
            actual_width, actual_height = validate_image_bytes(
                payload,
                expected_content_type=detected,
                min_width=policy.min_width,
                min_height=policy.min_height,
                max_width=policy.max_width,
                max_height=policy.max_height,
            )
            if policy.is_public:
                try:
                    payload, actual_width, actual_height = sanitize_public_image_bytes(
                        payload,
                        expected_content_type=detected,
                        min_width=policy.min_width,
                        min_height=policy.min_height,
                        max_width=policy.max_width,
                        max_height=policy.max_height,
                    )
                except MediaContentValidationError as exc:
                    raise MediaValidationError(str(exc), code=exc.code) from exc
                checksum = hashlib.sha256(payload).hexdigest()

        try:
            bucket = self.storage.bucket_for_type(policy.bucket_type)
            self.storage.ensure_bucket(bucket, public_read=policy.is_public)
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc
        object_key = self.generate_object_key(
            owner_user=user,
            purpose_rule=policy,
            filename=clean_filename,
        )
        try:
            stat = self.storage.put_bytes(
                bucket=bucket,
                object_key=object_key,
                data=payload,
                content_type=detected,
            )
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        try:
            doc = frappe.get_doc(
                {
                    "doctype": "AOS Media Object",
                    "owner_user": user,
                    "bucket": bucket,
                    "object_key": object_key,
                    "original_filename": clean_filename,
                    "content_type": detected,
                    "expected_size_bytes": len(payload),
                    "size_bytes": int(stat.size or len(payload)),
                    "etag": stat.etag or "",
                    "checksum": checksum,
                    "visibility": policy.visibility,
                    "purpose": policy.key,
                    "status": "Uploaded",
                    "uploaded_at": now_datetime(),
                    "completed_at": now_datetime(),
                    "width": int(actual_width or 0) or None,
                    "height": int(actual_height or 0) or None,
                    "duration_seconds": clean_duration,
                    "derived_from_media": str(derived_from_media or "").strip(),
                    "processing_job": str(processing_job or "").strip(),
                }
            )
            doc.insert(ignore_permissions=True)
        except Exception:
            try:
                self.storage.delete_object(bucket, object_key)
            except Exception:
                media_log(
                    "delete_failed",
                    purpose=policy.key,
                    operation="delete",
                    outcome="retryable_failure",
                    failure_category="compensation_failed",
                )
            raise

        media_log(
            "upload_completed",
            media_id=doc.name,
            purpose=policy.key,
            operation="process",
            bytes_count=len(payload),
        )
        return doc

    def create_uploaded_from_existing_object(
        self,
        *,
        user: str,
        purpose: str,
        filename: str,
        content_type: str,
        bucket: str,
        object_key: str,
        size_bytes: int | None = None,
        etag: str | None = None,
        width: int | None = None,
        height: int | None = None,
        duration_seconds: float | None = None,
        derived_from_media: str | None = None,
    ) -> object:
        policy = self._get_purpose_or_raise(purpose)
        clean_duration = self._validate_duration(policy, duration_seconds)
        clean_filename = self._normalize_filename(filename)
        claimed_type = self._normalize_content_type(content_type, clean_filename)
        validate_filename_extension(clean_filename, policy.allowed_extensions)
        clean_bucket = str(bucket or "").strip().strip("/")
        clean_key = str(object_key or "").strip().strip("/")
        expected_bucket = self.storage.bucket_for_type(policy.bucket_type)
        if clean_bucket != expected_bucket:
            raise MediaValidationError(
                "Storage object bucket does not match media purpose",
                code="INVALID_FILE",
            )
        if not clean_key.startswith(f"{policy.prefix}/"):
            raise MediaValidationError(
                "Storage object key does not match media purpose",
                code="INVALID_FILE",
            )

        try:
            stat = self.storage.stat_object(clean_bucket, clean_key)
        except StorageNotFoundError as exc:
            raise MediaNotFoundError("Storage object was not found") from exc
        except (StorageUnavailableError, StorageConfigurationError) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        declared_size = int(size_bytes or stat.size or 0)
        if declared_size and stat.size and declared_size != stat.size:
            raise MediaValidationError("Storage object size does not match callback metadata", code="SIZE_MISMATCH")

        transient_doc = _TransientMedia(
            content_type=claimed_type,
            size_bytes=stat.size,
            expected_size_bytes=stat.size,
            expected_checksum="",
        )
        validated = self._validate_confirmed_object(
            doc=transient_doc,
            purpose_rule=policy,
            stat=stat,
            bucket=clean_bucket,
            object_key=clean_key,
        )
        object_was_rewritten = validated.finalized_bytes is not None
        if object_was_rewritten:
            try:
                stat = self.storage.put_bytes(
                    bucket=clean_bucket,
                    object_key=clean_key,
                    data=validated.finalized_bytes,
                    content_type=validated.content_type,
                )
            except (StorageConfigurationError, StorageUnavailableError, StorageValidationError) as exc:
                raise MediaStorageError("Storage is temporarily unavailable") from exc
        if width and validated.width and int(width) != validated.width:
            raise MediaValidationError("Image width does not match object metadata", code="INVALID_FILE")
        if height and validated.height and int(height) != validated.height:
            raise MediaValidationError("Image height does not match object metadata", code="INVALID_FILE")

        doc = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": user,
                "bucket": clean_bucket,
                "object_key": clean_key,
                "original_filename": clean_filename,
                "content_type": validated.content_type,
                "expected_size_bytes": validated.size_bytes,
                "size_bytes": validated.size_bytes,
                "etag": (stat.etag or "") if object_was_rewritten else (etag or stat.etag or ""),
                "checksum": validated.checksum_sha256,
                "visibility": policy.visibility,
                "purpose": policy.key,
                "status": "Uploaded",
                "uploaded_at": now_datetime(),
                "completed_at": now_datetime(),
                "width": validated.width or int(width or 0) or None,
                "height": validated.height or int(height or 0) or None,
                "duration_seconds": clean_duration,
                "derived_from_media": str(derived_from_media or "").strip(),
            }
        )
        doc.insert(ignore_permissions=True)
        return doc

    # GET / URLS
    def get_media_doc(self, media_id: str):
        clean_id = str(media_id or "").strip()
        if not clean_id:
            raise MediaValidationError("Media id is required", code="VALIDATION_ERROR")
        if not frappe.db.exists("AOS Media Object", clean_id):
            raise MediaNotFoundError("Media not found")
        return frappe.get_doc("AOS Media Object", clean_id)

    def get_url(
        self,
        *,
        media_id: str,
        user: str | None = None,
        expiry_minutes: int | None = None,
    ) -> str:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_read(doc, user)
        if doc.status not in ACTIVE_READABLE_STATUSES:
            if doc.status == "Deleted":
                raise MediaNotFoundError("Media not found")
            raise MediaConflictError("Media is not ready", code="MEDIA_NOT_READY")

        if doc.visibility == "Public":
            return self._public_url_for_doc(doc)

        minutes = self._normalize_download_expiry(expiry_minutes)
        if getattr(doc, "purpose", None) == "verification_document":
            # Identity evidence is more sensitive than ordinary private media;
            # keep reviewer/owner signed access deliberately short-lived.
            minutes = min(minutes, 10)
        try:
            return self.storage.presigned_get_url(doc.bucket, doc.object_key, expiry_minutes=minutes)
        except StorageUnavailableError as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc

    def get_public_url(self, media_id: str) -> str:
        doc = self.get_media_doc(media_id)
        if doc.visibility != "Public" or doc.status not in ACTIVE_READABLE_STATUSES:
            return ""
        return self._public_url_for_doc(doc)

    def get_public_url_map(self, media_ids: Iterable[str]) -> dict[str, str]:
        """Resolve multiple readable public Media URLs with one database query.

        Missing, private, deleted, and otherwise unreadable rows are omitted.
        Storage configuration failures are isolated per item so public list
        serializers can fail closed without exposing internal storage details.
        """

        unique = sorted({str(media_id or "").strip() for media_id in media_ids if media_id})
        if not unique:
            return {}
        rows = frappe.get_all(
            "AOS Media Object",
            filters={
                "name": ["in", unique],
                "visibility": "Public",
                "status": ["in", sorted(ACTIVE_READABLE_STATUSES)],
            },
            fields=["name", "bucket", "object_key"],
            limit=max(1, len(unique)),
        )
        result: dict[str, str] = {}
        for row in rows:
            try:
                url = self._public_url_for_doc(row)
            except MediaError:
                continue
            if url:
                result[str(row.name)] = url
        return result

    # VALIDATE / ATTACH / REPLACE
    def validate_media_for_use(
        self,
        *,
        media_id: str,
        user: str,
        purpose: str,
        attached_doctype: str | None = None,
        attached_name: str | None = None,
    ) -> object:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        policy = self._get_purpose_or_raise(purpose)
        if doc.purpose != policy.key:
            raise MediaValidationError("Media has the wrong purpose", code="INVALID_MEDIA_PURPOSE")
        if doc.visibility != policy.visibility:
            raise MediaValidationError("Media visibility does not match its purpose", code="INVALID_FILE")
        if doc.status == "Uploaded" and not doc.attached_doctype and not doc.attached_name:
            return doc
        if (
            doc.status == "Attached"
            and attached_doctype
            and attached_name
            and doc.attached_doctype == attached_doctype
            and doc.attached_name == attached_name
        ):
            return doc
        if doc.status == "Attached":
            raise MediaConflictError("Media is already attached", code="MEDIA_ALREADY_ATTACHED")
        if doc.status == "Deleted":
            raise MediaNotFoundError("Media not found")
        raise MediaConflictError("Media is not ready for attachment", code="MEDIA_NOT_READY")

    def assert_media_ready_for_attach(self, *, media_id: str, user: str, purpose: str) -> object:
        return self.validate_media_for_use(media_id=media_id, user=user, purpose=purpose)

    def attach_media(
        self,
        *,
        media_id: str,
        user: str,
        purpose: str,
        attached_doctype: str,
        attached_name: str,
        attached_field: str | None = None,
        replacing_media_id: str | None = None,
        system: bool = False,
    ) -> object:
        policy = self._get_purpose_or_raise(purpose)
        try:
            assert_attachment_target_allowed(
                user=user,
                policy=policy,
                attached_doctype=attached_doctype,
                attached_name=attached_name,
                system=system,
            )
        except ResourceNotFoundError as exc:
            raise MediaNotFoundError("Attachment resource was not found", code="RESOURCE_NOT_FOUND") from exc
        except ResourceAuthorizationError as exc:
            raise MediaPermissionError(str(exc), code="MEDIA_ACCESS_DENIED") from exc

        # Lock the shared attachment target before the media row. Every contender
        # for a resource-level media limit therefore serializes on the same DB
        # row across Frappe workers/nodes; process-local locks are never used.
        self._lock_attachment_target(
            policy=policy,
            attached_doctype=attached_doctype,
            attached_name=attached_name,
        )
        self._lock_media_row(media_id)
        doc = self.validate_media_for_use(
            media_id=media_id,
            user=user,
            purpose=purpose,
            attached_doctype=attached_doctype,
            attached_name=attached_name,
        )

        if doc.status == "Attached":
            return doc
        if replacing_media_id and not policy.replacement_permitted and not system:
            raise MediaPermissionError(
                "Media replacement is not permitted for this purpose",
                code="MEDIA_ACCESS_DENIED",
            )
        self._enforce_attachment_count(
            policy=policy,
            attached_doctype=attached_doctype,
            attached_name=attached_name,
            excluding_media_ids={doc.name, str(replacing_media_id or "").strip()},
        )
        doc.status = "Attached"
        doc.attached_doctype = str(attached_doctype or "").strip()
        doc.attached_name = str(attached_name or "").strip()
        doc.attached_field = str(attached_field or "").strip()
        doc.attached_at = now_datetime()
        doc.save(ignore_permissions=True)
        media_log(
            "attachment_completed",
            media_id=doc.name,
            purpose=policy.key,
            operation="attach",
        )
        return doc

    def release_media(
        self,
        *,
        media_id: str,
        user: str,
        attached_doctype: str | None = None,
        attached_name: str | None = None,
        replacement_media_id: str | None = None,
        system: bool = False,
    ) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        if not system:
            self.assert_user_can_manage(doc, user)
        if doc.status in {"Deleted", "Delete Pending", "Replaced"}:
            return doc
        if attached_doctype and doc.attached_doctype != attached_doctype:
            raise MediaConflictError("Media attachment does not match resource", code="INVALID_STATE")
        if attached_name and doc.attached_name != attached_name:
            raise MediaConflictError("Media attachment does not match resource", code="INVALID_STATE")
        policy = self._get_purpose_or_raise(doc.purpose)
        if replacement_media_id and not policy.replacement_permitted and not system:
            raise MediaPermissionError(
                "Media replacement is not permitted for this purpose",
                code="MEDIA_ACCESS_DENIED",
            )
        doc.status = "Replaced" if replacement_media_id else "Orphaned"
        doc.replaced_by_media = str(replacement_media_id or "").strip()
        doc.replaced_at = now_datetime() if replacement_media_id else None
        doc.orphaned_at = now_datetime()
        doc.attached_doctype = ""
        doc.attached_name = ""
        doc.attached_field = ""
        doc.save(ignore_permissions=True)
        media_log(
            "replacement_completed" if replacement_media_id else "delete_requested",
            media_id=doc.name,
            purpose=doc.purpose,
            operation="replace" if replacement_media_id else "delete",
        )
        return doc

    # DELETE
    def delete_media(self, *, media_id: str, user: str, force: bool = False) -> object:
        if force:
            raise MediaPermissionError(
                "Forced deletion is reserved for system cleanup",
                code="MEDIA_ACCESS_DENIED",
            )
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        if doc.status == "Deleted":
            return doc
        if doc.status == "Delete Pending":
            self._enqueue_delete_after_commit(doc.name)
            return doc
        if doc.status == "Attached" or self._has_external_reference(doc.name):
            raise MediaConflictError(
                "Referenced media must be detached or replaced before deletion",
                code="MEDIA_ALREADY_ATTACHED",
            )
        policy = self._get_purpose_or_raise(doc.purpose)
        if not policy.deletion_permitted:
            raise MediaPermissionError("Media deletion is not permitted", code="MEDIA_ACCESS_DENIED")

        self._persist_delete_lifecycle(
            doc,
            {
                "status": "Delete Pending",
                "delete_requested_at": now_datetime(),
            },
            bypass_validation=False,
        )
        media_log("delete_requested", media_id=doc.name, purpose=doc.purpose, operation="delete")
        # Database state is authoritative. Object-store deletion is registered
        # for after commit so a rolled-back request cannot leave a live DB row
        # pointing at bytes that were already removed. The scheduled reconciler
        # also scans Delete Pending rows if queue registration/delivery is lost.
        self._enqueue_delete_after_commit(doc.name)
        return doc

    def delete_media_as_system(self, *, media_id: str) -> object:
        """Finalize deletion from a trusted worker/reconciliation context."""
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        if doc.status == "Deleted":
            return doc
        if doc.status == "Attached" or self._has_external_reference(doc.name):
            raise MediaConflictError(
                "Referenced media must be detached or replaced before deletion",
                code="MEDIA_ALREADY_ATTACHED",
            )
        if doc.status != "Delete Pending":
            self._persist_delete_lifecycle(
                doc,
                {"status": "Delete Pending", "delete_requested_at": now_datetime()},
                bypass_validation=True,
            )
        return self._finalize_delete(doc, system=True)

    def finalize_delete_as_system(self, *, media_id: str) -> object:
        """Delete object bytes for a committed Delete Pending media record."""
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        if doc.status == "Deleted":
            return doc
        if doc.status != "Delete Pending":
            return doc
        if doc.status == "Attached" or self._has_external_reference(doc.name):
            raise MediaConflictError(
                "Referenced media must be detached or replaced before deletion",
                code="MEDIA_ALREADY_ATTACHED",
            )
        return self._finalize_delete(doc, system=True)

    def _finalize_delete(self, doc, *, system: bool) -> object:
        try:
            self._delete_all_storage_identities(doc)
        except (StorageUnavailableError, StorageConfigurationError) as exc:
            self._record_storage_failure(doc, exc, bypass_validation=system)
            media_log(
                "delete_failed",
                media_id=doc.name,
                purpose=doc.purpose,
                operation="delete",
                outcome="retryable_failure",
                failure_category="storage_unavailable",
                retry_count=int(getattr(doc, "retry_count", 0) or 0),
            )
            raise MediaStorageError("Storage deletion will be retried") from exc

        deleted_values = {
            "status": "Deleted",
            "deleted_at": now_datetime(),
            "last_storage_error": "",
        }
        if getattr(doc, "upload_object_key", None):
            deleted_values["staging_cleanup_required"] = 1
        self._persist_delete_lifecycle(doc, deleted_values, bypass_validation=system)
        media_log("delete_completed", media_id=doc.name, purpose=doc.purpose, operation="delete")
        return doc

    def _enqueue_delete_after_commit(self, media_id: str) -> None:
        queue_name = str(get_env("AOS_MEDIA_DELETE_QUEUE_NAME", "short") or "short").strip() or "short"
        timeout = get_env_int(
            "AOS_MEDIA_DELETE_JOB_TIMEOUT_SECONDS", 120, min_value=30, max_value=1800
        )
        try:
            frappe.enqueue(
                "aos.tasks.media.finalize_media_deletion",
                queue=queue_name,
                media_id=str(media_id),
                job_id=f"aos-media-delete:{media_id}",
                enqueue_after_commit=True,
                timeout=timeout,
            )
        except Exception:
            # The committed Delete Pending row is itself a durable outbox. The
            # scheduled cleanup task will retry it without losing correctness.
            media_log(
                "delete_enqueue_failed",
                media_id=media_id,
                operation="delete",
                outcome="retryable_failure",
                failure_category="queue_unavailable",
            )

    def mark_delete_pending(self, *, media_id: str, user: str) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        if doc.status not in {"Deleted", "Delete Pending"}:
            doc.status = "Delete Pending"
            doc.delete_requested_at = now_datetime()
            doc.save(ignore_permissions=True)
        if doc.status == "Delete Pending":
            self._enqueue_delete_after_commit(doc.name)
        return doc

    # PROCESSING
    def mark_processing(self, *, media_id: str, user: str | None = None, system: bool = False) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        if not system:
            self.assert_user_can_manage(doc, str(user or ""))
        if doc.status not in {"Uploaded", "Attached", "Processing"}:
            raise MediaConflictError("Media cannot enter processing", code="INVALID_STATE")
        if doc.status != "Attached":
            doc.status = "Processing"
        doc.processing_started_at = now_datetime()
        doc.processing_completed_at = None
        doc.processing_error = ""
        doc.save(ignore_permissions=True)
        media_log("processing_started", media_id=doc.name, purpose=doc.purpose, operation="process")
        return doc

    def mark_processing_completed(self, *, media_id: str) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        if doc.status not in {"Processing", "Attached", "Ready"}:
            raise MediaConflictError("Media is not being processed", code="INVALID_STATE")
        if doc.status == "Processing":
            doc.status = "Ready"
        doc.processing_completed_at = now_datetime()
        doc.processing_error = ""
        doc.failure_code = ""
        doc.failure_reason = ""
        doc.save(ignore_permissions=True)
        media_log(
            "processing_completed",
            media_id=doc.name,
            purpose=doc.purpose,
            operation="process",
        )
        return doc

    def mark_processing_failed(self, *, media_id: str, reason: str) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        doc.processing_error = self._safe_failure_reason(reason)
        doc.processing_completed_at = now_datetime()
        if doc.status == "Processing":
            doc.status = "Failed"
            doc.failure_code = "PROCESSING_FAILED"
            doc.failure_reason = doc.processing_error
        doc.save(ignore_permissions=True)
        media_log(
            "processing_failed",
            media_id=doc.name,
            purpose=doc.purpose,
            operation="process",
            outcome="failure",
            failure_category="processing_failed",
        )
        return doc

    # CLEANUP
    def cleanup_expired_upload_sessions(self, *, limit: int = 100) -> int:
        """Close initialized uploads as soon as their server session expires.

        Multipart cleanup must be driven by ``upload_expires_at`` rather than
        record age so abandoned UploadPart data is aborted on the first hourly
        cleanup pass after expiry. The older age-based cleanup remains as a
        compatibility fallback for legacy rows without expiry metadata.
        """
        return self._cleanup_by_filters(
            filters={
                "status": "Initialized",
                "upload_expires_at": ["<", now_datetime()],
            },
            limit=limit,
        )

    def cleanup_initialized(self, *, older_than_hours: int = 24, limit: int = 100) -> int:
        cutoff = add_to_date(now_datetime(), hours=-max(1, int(older_than_hours)))
        return self._cleanup_by_filters(
            filters={"status": "Initialized", "creation": ["<", cutoff]},
            limit=limit,
        )

    def cleanup_unattached_uploaded(self, *, older_than_days: int = 7, limit: int = 100) -> int:
        cutoff = add_to_date(now_datetime(), days=-max(1, int(older_than_days)))
        return self._cleanup_by_filters(
            filters={
                "status": ["in", ["Uploaded", "Orphaned", "Replaced", "Failed"]],
                "modified": ["<", cutoff],
            },
            limit=limit,
        )

    def cleanup_delete_pending(self, *, older_than_hours: int = 1, limit: int = 100) -> int:
        cutoff = add_to_date(now_datetime(), hours=-max(1, int(older_than_hours)))
        return self._cleanup_by_filters(
            filters={"status": "Delete Pending", "delete_requested_at": ["<", cutoff]},
            limit=limit,
        )

    def cleanup_staging_objects(self, *, limit: int = 100) -> int:
        rows = frappe.get_all(
            "AOS Media Object",
            filters={"staging_cleanup_required": 1},
            fields=[
                "name",
                "purpose",
                "upload_bucket",
                "upload_object_key",
                "upload_expires_at",
            ],
            limit=max(1, min(int(limit or 100), 500)),
            order_by="upload_expires_at asc, modified asc",
        )
        cleaned = 0
        for row in rows:
            expires_at = getattr(row, "upload_expires_at", None)
            if expires_at:
                try:
                    if get_datetime(expires_at) > now_datetime():
                        continue
                except Exception:
                    continue
            try:
                if row.upload_bucket and row.upload_object_key:
                    self.storage.delete_object(row.upload_bucket, row.upload_object_key)
                frappe.db.set_value(
                    "AOS Media Object",
                    row.name,
                    {
                        "upload_bucket": "",
                        "upload_object_key": "",
                        "staging_cleanup_required": 0,
                    },
                    update_modified=False,
                )
                cleaned += 1
            except Exception:
                continue
        if cleaned:
            frappe.db.commit()
        return cleaned

    def _cleanup_by_filters(self, *, filters: dict, limit: int) -> int:
        if not frappe.db.exists("DocType", "AOS Media Object"):
            return 0
        rows = frappe.get_all(
            "AOS Media Object",
            filters=filters,
            fields=["name", "status", "purpose", "attached_doctype", "attached_name"],
            limit=max(1, min(int(limit or 100), 500)),
            order_by="creation asc",
        )
        cleaned = 0
        for row in rows:
            if row.attached_doctype or row.attached_name or self._has_external_reference(row.name):
                continue
            try:
                self.delete_media_as_system(media_id=row.name)
                cleaned += 1
            except MediaStorageError:
                continue
            except Exception:
                frappe.log_error(frappe.get_traceback(), "AOS Media Cleanup Row Failed")
        if cleaned:
            frappe.db.commit()
            media_log(
                "cleanup_completed",
                operation="cleanup",
            )
        return cleaned

    @staticmethod
    def _has_external_reference(media_id: str) -> bool:
        """Defend cleanup/deletion against metadata drift in feature tables."""
        references = (
            ("AOS Profile", "profile_image_media"),
            ("AOS Seller", "shop_banner_media"),
            ("AOS Ad", "video_media"),
            ("AOS Ad Image", "media"),
            ("AOS Category", "icon_media"),
            ("AOS Live Stream", "live_cover_media"),
            ("AOS Message Attachment", "media"),
            ("AOS Review Image", "media"),
            ("AOS Short", "raw_video_media"),
            ("AOS Short", "thumbnail_media"),
            ("AOS Sound", "sound_media"),
            ("AOS Verification Document", "media"),
            ("AOS Video Processing Job", "raw_video_media"),
            ("AOS Video Processing Job", "thumbnail_media"),
        )
        for doctype, fieldname in references:
            try:
                if not frappe.db.table_exists(doctype):
                    continue
                if frappe.db.exists(doctype, {fieldname: media_id}):
                    return True
            except Exception:
                # Fail safe: an unknown reference state must not authorize deletion.
                return True

        # A processing job is also an active reference. Deleting its source while
        # a worker is queued/running/retrying can otherwise produce intermittent
        # failures across nodes or, worse, race with a derivative commit.
        try:
            if frappe.db.table_exists("AOS Media Processing Job") and frappe.db.exists(
                "AOS Media Processing Job",
                {
                    "source_media": media_id,
                    "status": ["in", ["Queued", "Processing", "Retry Waiting"]],
                },
            ):
                return True
        except Exception:
            return True
        return False

    # PERMISSIONS
    def assert_user_can_manage(self, doc, user: str) -> None:
        clean_user = str(user or "").strip()
        if not clean_user or clean_user == "Guest":
            raise MediaPermissionError("Authentication is required", code="AUTH_REQUIRED")
        if doc.owner_user == clean_user:
            return

        attached_doctype = str(getattr(doc, "attached_doctype", "") or "").strip()
        attached_name = str(getattr(doc, "attached_name", "") or "").strip()
        if attached_doctype and attached_name and has_doctype_permission(
            user=clean_user,
            doctype=attached_doctype,
            ptype="write",
            docname=attached_name,
        ):
            return

        # Unattached administrative media operations are controlled by the
        # AOS Media Object DocType permissions, not by a hardcoded role name.
        if has_doctype_permission(
            user=clean_user,
            doctype="AOS Media Object",
            ptype="write",
            doc=doc,
        ):
            return
        raise MediaPermissionError("You cannot manage this media", code="MEDIA_OWNERSHIP_REQUIRED")

    def assert_user_can_read(self, doc, user: str | None) -> None:
        if doc.visibility == "Public":
            return
        clean_user = str(user or "").strip()
        if not clean_user or clean_user == "Guest":
            raise MediaPermissionError("Authentication is required", code="AUTH_REQUIRED")
        if getattr(doc, "purpose", None) == "verification_document":
            # Before submission the uploader may preview their confirmed upload.
            # Once attached, access is limited to the request owner or a user
            # who has read permission on the Verification Request through
            # Frappe's permission engine. Released/orphaned evidence is not
            # readable merely because the account originally uploaded it.
            if doc.owner_user == clean_user and doc.status == "Uploaded" and not doc.attached_name:
                return
            if self._user_can_read_verification_document(doc, clean_user):
                return
            raise MediaPermissionError("You cannot access this media", code="MEDIA_ACCESS_DENIED")
        if doc.owner_user == clean_user:
            return

        attached_doctype = str(getattr(doc, "attached_doctype", "") or "").strip()
        attached_name = str(getattr(doc, "attached_name", "") or "").strip()
        if attached_doctype and attached_name and has_doctype_permission(
            user=clean_user,
            doctype=attached_doctype,
            ptype="read",
            docname=attached_name,
        ):
            return
        if has_doctype_permission(
            user=clean_user,
            doctype="AOS Media Object",
            ptype="read",
            doc=doc,
        ):
            return
        if self._user_can_read_chat_attachment(doc, clean_user):
            return
        raise MediaPermissionError("You cannot access this media", code="MEDIA_ACCESS_DENIED")

    def _user_can_read_chat_attachment(self, doc, user: str) -> bool:
        if getattr(doc, "purpose", None) != "chat_attachment":
            return False
        try:
            rows = frappe.db.sql(
                """
                SELECT a.name
                FROM `tabAOS Message Attachment` a
                INNER JOIN `tabAOS Message` m ON m.name = a.message
                INNER JOIN `tabAOS Conversation` c ON c.name = m.conversation
                WHERE a.media = %(media)s
                  AND IFNULL(m.deleted_for_everyone, 0) = 0
                  AND (
                    (c.participant_1 = %(user)s AND IFNULL(m.deleted_for_1, 0) = 0)
                    OR
                    (c.participant_2 = %(user)s AND IFNULL(m.deleted_for_2, 0) = 0)
                  )
                LIMIT 1
                """,
                {"media": doc.name, "user": user},
                as_dict=True,
            )
            return bool(rows)
        except Exception:
            return False

    def _user_can_read_verification_document(self, doc, user: str) -> bool:
        if getattr(doc, "purpose", None) != "verification_document":
            return False
        if doc.attached_doctype != "AOS Verification Request" or not doc.attached_name:
            return False
        if has_doctype_permission(
            user=user,
            doctype="AOS Verification Request",
            ptype="read",
            docname=doc.attached_name,
        ):
            return True
        try:
            return frappe.db.get_value("AOS Verification Request", doc.attached_name, "user") == user
        except Exception:
            return False

    # INTERNAL HELPERS
    def _get_purpose_or_raise(self, purpose: str) -> MediaPurpose:
        policy = get_media_purpose(purpose)
        if not policy:
            raise MediaValidationError("Invalid media purpose", code="INVALID_MEDIA_PURPOSE")
        return policy

    def _assert_purpose_upload_allowed(self, *, user: str, policy: MediaPurpose, system: bool) -> None:
        try:
            assert_purpose_upload_allowed(user=user, policy=policy, system=system)
        except ResourceAuthorizationError as exc:
            raise MediaPermissionError(str(exc), code="MEDIA_ACCESS_DENIED") from exc

    def _normalize_filename(self, filename: str) -> str:
        try:
            return normalize_filename(filename)
        except MediaContentValidationError as exc:
            raise MediaValidationError(str(exc), code=exc.code) from exc

    def _normalize_content_type(self, content_type: str, filename: str) -> str:
        clean = normalize_content_type(content_type)
        if not clean:
            raise MediaValidationError("Content type is required", code="UNSUPPORTED_MEDIA_TYPE")
        return clean

    def _normalize_checksum(self, checksum: str | None) -> str:
        try:
            return normalize_checksum(checksum)
        except MediaContentValidationError as exc:
            raise MediaValidationError(str(exc), code=exc.code) from exc

    def _normalize_size(self, size_bytes: object) -> int:
        try:
            size = int(size_bytes)
        except (TypeError, ValueError) as exc:
            raise MediaValidationError("File size is required", code="VALIDATION_ERROR") from exc
        if size <= 0:
            raise MediaValidationError("File size must be greater than zero", code="INVALID_FILE")
        return size

    def _validate_upload_request(
        self,
        *,
        purpose_rule: MediaPurpose,
        content_type: str,
        size_bytes: int,
    ) -> None:
        if normalize_content_type(content_type) not in purpose_rule.allowed_content_types:
            raise MediaValidationError("Unsupported file type", code="UNSUPPORTED_MEDIA_TYPE")
        if size_bytes <= 0:
            raise MediaValidationError("File is empty", code="INVALID_FILE")
        if size_bytes > purpose_rule.max_size_bytes:
            raise MediaValidationError("File is too large", code="FILE_TOO_LARGE")

    @staticmethod
    def _validate_duration(policy: MediaPurpose, duration_seconds: float | None) -> float | None:
        if duration_seconds in (None, ""):
            return None
        try:
            duration = float(duration_seconds)
        except (TypeError, ValueError) as exc:
            raise MediaValidationError("Media duration is invalid", code="INVALID_FILE") from exc
        if duration <= 0:
            raise MediaValidationError("Media duration is invalid", code="INVALID_FILE")
        if policy.max_duration_seconds and duration > policy.max_duration_seconds:
            raise MediaValidationError("Media duration exceeds the allowed limit", code="INVALID_FILE")
        return duration

    def _select_upload_mode(
        self,
        *,
        policy: MediaPurpose,
        size_bytes: int,
        requested_mode: str | None,
    ) -> str:
        explicit_mode = requested_mode not in (None, "")
        requested = str(requested_mode or "direct").strip().lower().replace("-", "_")
        if requested == "single":
            requested = "direct"
        if requested not in {"auto", "direct", "multipart"}:
            raise MediaValidationError("Invalid upload mode", code="VALIDATION_ERROR")

        supports_multipart = bool(
            int(getattr(policy, "multipart_part_size_bytes", 0) or 0)
            and int(getattr(policy, "multipart_threshold_bytes", 0) or 0)
        )
        threshold = int(getattr(policy, "multipart_threshold_bytes", 0) or 0)
        multipart_required = supports_multipart and threshold > 0 and int(size_bytes) >= threshold

        # Do not silently change the transport contract under older clients.
        # Large-file clients must explicitly opt into the resumable contract by
        # sending upload_mode=auto/multipart. A legacy client that only knows
        # about one upload_url fails before any bytes are sent instead of being
        # handed a null direct URL or a fragile oversized single PUT.
        if multipart_required and not explicit_mode:
            raise MediaValidationError(
                "Large upload requires a multipart-capable client",
                code="MULTIPART_REQUIRED",
            )

        if requested == "multipart" and not supports_multipart:
            raise MediaValidationError(
                "Multipart upload is not supported for this media purpose",
                code="MULTIPART_NOT_SUPPORTED",
            )
        if requested == "direct" and multipart_required:
            raise MediaValidationError(
                "Multipart upload is required for this file",
                code="MULTIPART_REQUIRED",
            )
        if requested == "multipart" or (requested == "auto" and multipart_required):
            return "multipart"
        return "direct"

    @staticmethod
    def _multipart_part_size(policy: MediaPurpose) -> int:
        size = int(getattr(policy, "multipart_part_size_bytes", 0) or 0)
        if size < MIN_MULTIPART_PART_SIZE_BYTES or size > 5 * 1024 * 1024 * 1024:
            raise MediaValidationError(
                "Multipart upload configuration is invalid",
                code="INVALID_STATE",
            )
        return size

    @staticmethod
    def _multipart_part_count(*, size_bytes: int, part_size_bytes: int) -> int:
        count = int(math.ceil(int(size_bytes) / int(part_size_bytes)))
        if count < 1 or count > MAX_MULTIPART_PARTS:
            raise MediaValidationError(
                "File requires too many multipart upload parts",
                code="FILE_TOO_LARGE",
            )
        return count

    def _enforce_active_multipart_limit(self, *, user: str) -> None:
        limit = get_env_int(
            "AOS_MEDIA_MULTIPART_ACTIVE_LIMIT_PER_USER",
            6,
            min_value=1,
            max_value=50,
        )
        media = frappe.qb.DocType("AOS Media Object")
        active = (
            frappe.qb.from_(media)
            .select(Count("*"))
            .where(
                (media.owner_user == user)
                & (media.status == "Initialized")
                & (media.upload_mode == "multipart")
                & (media.upload_expires_at > now_datetime())
            )
            .run()[0][0]
        )
        if int(active or 0) >= limit:
            raise MediaConflictError(
                "Too many active multipart uploads",
                code="MULTIPART_ACTIVE_LIMIT",
            )

    @staticmethod
    def _assert_multipart_mode(doc) -> None:
        if str(getattr(doc, "upload_mode", "") or "direct").strip().lower() != "multipart":
            raise MediaValidationError(
                "Media does not use multipart upload",
                code="MULTIPART_NOT_SUPPORTED",
            )

    def _assert_multipart_session_active(self, doc) -> None:
        if doc.status != "Initialized":
            raise MediaConflictError(
                "Multipart upload is not active",
                code="INVALID_STATE",
            )
        if getattr(doc, "multipart_completed_at", None):
            raise MediaConflictError(
                "Multipart upload has already been completed",
                code="MULTIPART_ALREADY_COMPLETED",
            )
        if self._upload_is_expired(doc):
            self._abort_multipart_storage_best_effort(doc)
            self._mark_failed(
                doc,
                code="UPLOAD_EXPIRED",
                reason="Multipart upload completion window expired",
            )
            raise MediaValidationError("Upload has expired", code="UPLOAD_EXPIRED")

    def _normalize_part_url_window(
        self,
        *,
        total_parts: int,
        start_part: int | None,
        count: int | None,
    ) -> tuple[int, int]:
        try:
            start = int(1 if start_part in (None, "") else start_part)
            requested_count = int(
                self.get_multipart_part_url_batch_size()
                if count in (None, "")
                else count
            )
        except (TypeError, ValueError) as exc:
            raise MediaValidationError(
                "Multipart part window is invalid",
                code="VALIDATION_ERROR",
            ) from exc
        if start < 1 or start > int(total_parts or 0):
            raise MediaValidationError(
                "Multipart start part is invalid",
                code="VALIDATION_ERROR",
            )
        if requested_count < 1 or requested_count > MAX_MULTIPART_PART_URL_BATCH:
            raise MediaValidationError(
                "Multipart part URL batch size is invalid",
                code="VALIDATION_ERROR",
            )
        return start, min(requested_count, int(total_parts) - start + 1)

    @staticmethod
    def _expected_multipart_part_size(doc, part_number: int) -> int:
        part_size = int(getattr(doc, "multipart_part_size_bytes", 0) or 0)
        part_count = int(getattr(doc, "multipart_part_count", 0) or 0)
        expected_size = int(getattr(doc, "expected_size_bytes", 0) or 0)
        number = int(part_number)
        if part_size < MIN_MULTIPART_PART_SIZE_BYTES or part_count < 1:
            raise MediaConflictError(
                "Multipart upload metadata is invalid",
                code="INVALID_STATE",
            )
        if number < 1 or number > part_count:
            raise MediaValidationError(
                "Multipart part number is invalid",
                code="VALIDATION_ERROR",
            )
        if number < part_count:
            return part_size
        consumed = part_size * (part_count - 1)
        final_size = expected_size - consumed
        if final_size <= 0 or final_size > part_size:
            raise MediaConflictError(
                "Multipart upload metadata is invalid",
                code="INVALID_STATE",
            )
        return final_size

    def _validate_complete_multipart_parts(
        self,
        *,
        doc,
        parts: list[MultipartPart],
    ) -> None:
        expected_count = int(getattr(doc, "multipart_part_count", 0) or 0)
        ordered = sorted(parts or [], key=lambda item: int(item.part_number))
        if len(ordered) != expected_count:
            raise MediaConflictError(
                "Multipart upload is incomplete",
                code="MULTIPART_INCOMPLETE",
            )
        numbers = [int(item.part_number) for item in ordered]
        if numbers != list(range(1, expected_count + 1)):
            raise MediaConflictError(
                "Multipart upload is incomplete",
                code="MULTIPART_INCOMPLETE",
            )
        for part in ordered:
            if not str(part.etag or "").strip():
                raise MediaConflictError(
                    "Multipart upload is incomplete",
                    code="MULTIPART_INCOMPLETE",
                )
            expected_size = self._expected_multipart_part_size(doc, int(part.part_number))
            if int(part.size or 0) != expected_size:
                raise MediaValidationError(
                    "Multipart part size does not match the upload contract",
                    code="MULTIPART_PART_SIZE_MISMATCH",
                )

    def _multipart_status_payload(
        self,
        doc,
        *,
        uploaded_parts: list[int],
        bytes_uploaded: int,
        storage_complete: bool,
        invalid_parts: list[int] | None = None,
    ) -> dict[str, object]:
        part_count = int(getattr(doc, "multipart_part_count", 0) or 0)
        valid_uploaded = sorted(
            {
                int(number)
                for number in uploaded_parts
                if 1 <= int(number) <= part_count
            }
        )
        invalid = sorted(
            {
                int(number)
                for number in (invalid_parts or [])
                if 1 <= int(number) <= part_count
            }
        )
        missing = [
            number
            for number in range(1, part_count + 1)
            if number not in set(valid_uploaded)
        ]
        retry_parts = sorted(set(missing).union(invalid))
        expected_bytes = int(getattr(doc, "expected_size_bytes", 0) or 0)
        safe_uploaded = max(0, min(int(bytes_uploaded or 0), expected_bytes))
        if doc.status in ACTIVE_READABLE_STATUSES:
            state = "completed"
        elif doc.status == "Failed":
            state = "failed"
        elif storage_complete:
            state = "storage_completed"
        else:
            state = "uploading"
        return {
            "media_id": doc.name,
            "upload_mode": "multipart",
            "state": state,
            "media_status": doc.status,
            "part_size_bytes": int(getattr(doc, "multipart_part_size_bytes", 0) or 0),
            "part_count": part_count,
            "uploaded_parts": valid_uploaded,
            "missing_parts": missing,
            "invalid_parts": invalid,
            # Frontends should drive resume from this union rather than having to
            # remember that an uploaded-but-wrong-sized part must be overwritten
            # using the same part number.
            "retry_parts": retry_parts,
            "bytes_uploaded": safe_uploaded,
            "expected_size_bytes": expected_bytes,
            "progress": (safe_uploaded / expected_bytes) if expected_bytes > 0 else 0.0,
            "storage_complete": bool(storage_complete),
            "complete_ready": bool(storage_complete or (not retry_parts and part_count > 0)),
            "session_expires_in": self._remaining_upload_session_seconds(doc),
            "max_parallel_parts": self.get_multipart_max_parallel_parts(),
            "part_url_batch_size": self.get_multipart_part_url_batch_size(),
            "failure_code": str(getattr(doc, "failure_code", "") or "") or None,
        }

    def _validate_confirmed_object(
        self,
        *,
        doc,
        purpose_rule: MediaPurpose,
        stat: ObjectStat,
        bucket: str,
        object_key: str,
    ) -> "_ValidatedObject":
        if stat.size <= 0:
            raise MediaValidationError("Uploaded file is empty", code="INVALID_FILE")
        if stat.size > purpose_rule.max_size_bytes:
            raise MediaValidationError("Uploaded file is too large", code="FILE_TOO_LARGE")
        expected_size = int(getattr(doc, "expected_size_bytes", 0) or getattr(doc, "size_bytes", 0) or 0)
        if expected_size and stat.size != expected_size:
            raise MediaValidationError("Uploaded file size does not match initiation", code="SIZE_MISMATCH")

        claimed = normalize_content_type(getattr(doc, "content_type", ""))
        stored_type = normalize_content_type(getattr(stat, "content_type", ""))
        if claimed not in purpose_rule.allowed_content_types:
            raise MediaValidationError("Uploaded file type is not allowed", code="UNSUPPORTED_MEDIA_TYPE")
        if stored_type and stored_type != claimed:
            compatible = {stored_type, claimed} <= {"video/mp4", "video/quicktime"}
            if not compatible:
                raise MediaValidationError(
                    "Uploaded object content type does not match initiation",
                    code="CONTENT_TYPE_MISMATCH",
                )
        payload: bytes | None = None
        finalized_bytes: bytes | None = None
        try:
            head = self.storage.get_range(bucket, object_key, length=64 * 1024)
            detected = validate_magic_type(
                head=head,
                claimed_content_type=claimed,
                allowed_content_types=purpose_rule.allowed_content_types,
            )
            width = None
            height = None
            duration_seconds = None
            if detected.startswith("video/"):
                duration_seconds = extract_iso_bmff_duration_seconds(
                    read_range=lambda offset, length: self.storage.get_range(
                        bucket, object_key, offset=offset, length=length
                    ),
                    size_bytes=int(stat.size),
                    max_duration_seconds=purpose_rule.max_duration_seconds,
                )
            if detected.startswith("image/"):
                payload = self.storage.get_bytes(
                    bucket, object_key, max_bytes=purpose_rule.max_size_bytes
                )
                checksum = hashlib.sha256(payload).hexdigest()
                width, height = validate_image_bytes(
                    payload,
                    expected_content_type=detected,
                    min_width=purpose_rule.min_width,
                    min_height=purpose_rule.min_height,
                    max_width=purpose_rule.max_width,
                    max_height=purpose_rule.max_height,
                )
                actual_size = len(payload)
            else:
                expected_checksum = str(getattr(doc, "expected_checksum", "") or "").strip().lower()
                if expected_checksum or not purpose_rule.processing_required:
                    checksum, actual_size = sha256_chunks(
                        self.storage.iter_chunks(bucket, object_key, chunk_size=CHUNK_SIZE),
                        content_type=detected,
                    )
                else:
                    checksum = ""
                    actual_size = int(stat.size)
        except StorageNotFoundError as exc:
            raise MediaValidationError("Uploaded object is incomplete", code="UPLOAD_INCOMPLETE") from exc
        except (StorageConfigurationError, StorageUnavailableError, StorageValidationError) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        if actual_size != stat.size:
            raise MediaValidationError("Uploaded file changed during verification", code="UPLOAD_INCOMPLETE")
        expected_checksum = str(getattr(doc, "expected_checksum", "") or "").strip().lower()
        if expected_checksum and checksum != expected_checksum:
            raise MediaValidationError("Uploaded file checksum does not match", code="CHECKSUM_MISMATCH")

        if detected.startswith("image/") and purpose_rule.is_public and payload is not None:
            try:
                finalized_bytes, width, height = sanitize_public_image_bytes(
                    payload,
                    expected_content_type=detected,
                    min_width=purpose_rule.min_width,
                    min_height=purpose_rule.min_height,
                    max_width=purpose_rule.max_width,
                    max_height=purpose_rule.max_height,
                )
            except MediaContentValidationError as exc:
                raise MediaValidationError(str(exc), code=exc.code) from exc
            checksum = hashlib.sha256(finalized_bytes).hexdigest()
            final_size = len(finalized_bytes)
        else:
            final_size = actual_size

        return _ValidatedObject(
            content_type=detected,
            size_bytes=final_size,
            source_size_bytes=actual_size,
            checksum_sha256=checksum,
            width=width,
            height=height,
            duration_seconds=duration_seconds,
            finalized_bytes=finalized_bytes,
        )

    def _finalize_staged_object(
        self,
        *,
        doc,
        source_bucket: str,
        source_object_key: str,
        expected_size: int,
        finalized_bytes: bytes | None = None,
        content_type: str | None = None,
    ) -> ObjectStat:
        try:
            if finalized_bytes is not None:
                final_stat = self.storage.put_bytes(
                    bucket=doc.bucket,
                    object_key=doc.object_key,
                    data=finalized_bytes,
                    content_type=str(content_type or "application/octet-stream"),
                )
            elif source_bucket == doc.bucket and source_object_key == doc.object_key:
                final_stat = self.storage.stat_object(doc.bucket, doc.object_key)
            else:
                final_stat = self.storage.copy_object(
                    source_bucket=source_bucket,
                    source_object_key=source_object_key,
                    destination_bucket=doc.bucket,
                    destination_object_key=doc.object_key,
                )
        except StorageNotFoundError as exc:
            raise MediaValidationError("Staged upload is incomplete", code="UPLOAD_INCOMPLETE") from exc
        except (StorageConfigurationError, StorageUnavailableError, StorageValidationError) as exc:
            self._record_storage_failure(doc, exc)
            raise MediaStorageError("Storage is temporarily unavailable") from exc
        if int(final_stat.size or 0) != int(expected_size):
            try:
                self.storage.delete_object(doc.bucket, doc.object_key)
            except Exception:
                pass
            raise MediaValidationError("Finalized media size mismatch", code="UPLOAD_INCOMPLETE")
        return final_stat

    def _cleanup_staging_after_finalize(self, doc) -> None:
        """Remove staging bytes and close the upload identity safely.

        A direct presigned PUT cannot be revoked, so its staging key is retained
        behind a cleanup marker until the URL expires. Multipart UploadPart URLs
        are bound to an upload id that becomes invalid after completion; those
        identities can be cleared immediately after the staging object is copied.
        """
        upload_bucket, upload_key = self._upload_identity(doc)
        if upload_bucket == doc.bucket and upload_key == doc.object_key:
            if str(getattr(doc, "upload_mode", "") or "direct").lower() == "multipart":
                # Multipart completion invalidates every UploadPart URL because
                # the upload id is closed. Private heavy Shorts therefore upload
                # directly to their canonical key and can discard the control-
                # plane identity immediately without a redundant object copy.
                values = {
                    "upload_bucket": "",
                    "upload_object_key": "",
                    "multipart_upload_id": "",
                    "staging_cleanup_required": 0,
                }
                frappe.db.set_value(
                    "AOS Media Object",
                    doc.name,
                    values,
                    update_modified=False,
                )
                for fieldname, value in values.items():
                    setattr(doc, fieldname, value)
            return
        try:
            self.storage.delete_object(upload_bucket, upload_key)
        except Exception:
            pass

        if str(getattr(doc, "upload_mode", "") or "direct").lower() == "multipart":
            values = {
                "upload_bucket": "",
                "upload_object_key": "",
                "multipart_upload_id": "",
                "staging_cleanup_required": 0,
            }
            frappe.db.set_value(
                "AOS Media Object",
                doc.name,
                values,
                update_modified=False,
            )
            for fieldname, value in values.items():
                setattr(doc, fieldname, value)
            return

        frappe.db.set_value(
            "AOS Media Object",
            doc.name,
            "staging_cleanup_required",
            1,
            update_modified=False,
        )
        doc.staging_cleanup_required = 1

    def _abort_multipart_storage_best_effort(self, doc) -> None:
        if str(getattr(doc, "upload_mode", "") or "direct").lower() != "multipart":
            return
        upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
        if not upload_id:
            return
        try:
            upload_bucket, upload_key = self._upload_identity(doc)
            self.storage.abort_multipart_upload(
                upload_bucket,
                upload_key,
                upload_id=upload_id,
            )
        except Exception:
            pass

    def _delete_staging_best_effort(self, doc) -> None:
        self._abort_multipart_storage_best_effort(doc)
        try:
            upload_bucket, upload_key = self._upload_identity(doc)
            self.storage.delete_object(upload_bucket, upload_key)
        except Exception:
            pass
        try:
            doc.staging_cleanup_required = 1
            doc.save(ignore_permissions=True)
        except Exception:
            pass

    def _delete_all_storage_identities(self, doc) -> None:
        if str(getattr(doc, "upload_mode", "") or "direct").lower() == "multipart":
            upload_id = str(getattr(doc, "multipart_upload_id", "") or "").strip()
            if upload_id:
                upload_bucket, upload_key = self._upload_identity(doc)
                self.storage.abort_multipart_upload(
                    upload_bucket,
                    upload_key,
                    upload_id=upload_id,
                )

        identities = {(str(doc.bucket or ""), str(doc.object_key or ""))}
        upload_bucket = str(getattr(doc, "upload_bucket", "") or "")
        upload_key = str(getattr(doc, "upload_object_key", "") or "")
        if upload_bucket and upload_key:
            identities.add((upload_bucket, upload_key))
        for bucket, object_key in identities:
            if bucket and object_key:
                self.storage.delete_object(bucket, object_key)

    def _persist_delete_lifecycle(
        self,
        doc,
        values: dict[str, object],
        *,
        bypass_validation: bool,
    ) -> None:
        """Persist deletion-only state without revalidating immutable legacy metadata.

        System cleanup must be able to remove old orphan rows whose stored MIME or
        filename predates the canonical purpose policy. Only the tightly bounded
        lifecycle fields supplied by the deletion path use this bypass; normal
        user-managed saves continue through full DocType validation.
        """

        allowed_fields = {
            "status",
            "delete_requested_at",
            "deleted_at",
            "last_storage_error",
            "staging_cleanup_required",
        }
        if not set(values).issubset(allowed_fields):
            raise MediaConflictError(
                "Invalid system media lifecycle update",
                code="INVALID_STATE",
            )

        if bypass_validation:
            frappe.db.set_value(
                "AOS Media Object",
                doc.name,
                values,
                update_modified=True,
            )
            for fieldname, value in values.items():
                setattr(doc, fieldname, value)
            return

        for fieldname, value in values.items():
            setattr(doc, fieldname, value)
        doc.save(ignore_permissions=True)

    def _record_storage_failure(
        self,
        doc,
        exc: Exception,
        *,
        bypass_validation: bool = False,
    ) -> None:
        try:
            values = {
                "retry_count": int(getattr(doc, "retry_count", 0) or 0) + 1,
                "last_storage_error": str(getattr(exc, "category", "unavailable"))[:64],
                "last_storage_attempt_at": now_datetime(),
            }
            if bypass_validation:
                frappe.db.set_value(
                    "AOS Media Object",
                    doc.name,
                    values,
                    update_modified=True,
                )
                for fieldname, value in values.items():
                    setattr(doc, fieldname, value)
                return
            for fieldname, value in values.items():
                setattr(doc, fieldname, value)
            doc.save(ignore_permissions=True)
        except Exception:
            pass

    def _mark_failed(self, doc, *, code: str, reason: str) -> None:
        try:
            doc.status = "Failed"
            doc.failure_code = str(code or "INVALID_FILE")[:64]
            doc.failure_reason = self._safe_failure_reason(reason)
            doc.failed_at = now_datetime()
            doc.save(ignore_permissions=True)
        except Exception:
            pass

    @staticmethod
    def _safe_failure_reason(reason: object) -> str:
        text = str(reason or "Media operation failed").strip()
        return text[:240]

    def _upload_identity(self, doc) -> tuple[str, str]:
        return (
            str(getattr(doc, "upload_bucket", "") or doc.bucket).strip(),
            str(getattr(doc, "upload_object_key", "") or doc.object_key).strip(),
        )

    def _upload_is_expired(self, doc) -> bool:
        value = getattr(doc, "upload_expires_at", None)
        if not value:
            return False
        try:
            return get_datetime(value) < now_datetime()
        except Exception:
            return False

    def _public_url_for_doc(self, doc) -> str:
        try:
            return self.storage.build_public_url(doc.bucket, doc.object_key)
        except (StorageConfigurationError, StorageValidationError) as exc:
            raise MediaStorageError("Public media URL is unavailable") from exc

    def _normalize_download_expiry(self, expiry_minutes: int | None) -> int:
        config_default = get_media_download_expiry_minutes()
        try:
            minutes = int(expiry_minutes or config_default)
        except (TypeError, ValueError):
            minutes = config_default
        return max(1, min(minutes, 60))

    def get_upload_expiry_minutes(self, purpose_rule: MediaPurpose | None = None) -> int:
        if purpose_rule and purpose_rule.upload_expiry_minutes:
            return max(1, min(int(purpose_rule.upload_expiry_minutes), 60))
        try:
            settings = get_aos_settings_snapshot()
            return max(1, min(int(settings.media_presigned_upload_expiry_minutes or 10), 60))
        except Exception:
            return 10

    def get_upload_session_expiry_minutes(
        self,
        purpose_rule: MediaPurpose,
        *,
        upload_mode: str,
    ) -> int:
        if str(upload_mode or "direct").lower() != "multipart":
            return self.get_upload_expiry_minutes(purpose_rule)
        default_hours = max(1, min(int(getattr(purpose_rule, "multipart_session_expiry_hours", 24) or 24), 72))
        hours = get_env_int(
            "AOS_MEDIA_MULTIPART_SESSION_HOURS",
            default_hours,
            min_value=1,
            max_value=72,
        )
        return int(hours) * 60

    def _remaining_upload_session_seconds(self, doc) -> int:
        value = getattr(doc, "upload_expires_at", None)
        if not value:
            return 0
        try:
            seconds = int((get_datetime(value) - now_datetime()).total_seconds())
        except Exception:
            return 0
        return max(0, seconds)

    @staticmethod
    def get_multipart_part_url_batch_size() -> int:
        return get_env_int(
            "AOS_MEDIA_MULTIPART_PART_URL_BATCH_SIZE",
            8,
            min_value=1,
            max_value=MAX_MULTIPART_PART_URL_BATCH,
        )

    @staticmethod
    def get_multipart_max_parallel_parts() -> int:
        return get_env_int(
            "AOS_MEDIA_MULTIPART_MAX_PARALLEL_PARTS",
            4,
            min_value=1,
            max_value=8,
        )

    def _find_reusable_initiated_upload(
        self,
        *,
        user: str,
        purpose: str,
        idempotency_hash: str,
    ):
        rows = frappe.get_all(
            "AOS Media Object",
            filters={
                "owner_user": user,
                "purpose": purpose,
                "idempotency_key_hash": idempotency_hash,
                "status": "Initialized",
            },
            fields=["name"],
            order_by="creation desc",
            limit=1,
        )
        if not rows:
            return None
        doc = frappe.get_doc("AOS Media Object", rows[0].name)
        if self._upload_is_expired(doc):
            return None
        return doc

    @staticmethod
    def _assert_idempotent_upload_matches(
        doc,
        *,
        filename: str,
        content_type: str,
        size_bytes: int,
        duration_seconds: float | None,
        checksum_sha256: str,
        upload_mode: str,
    ) -> None:
        """Fail closed when an idempotency key is reused for different bytes.

        A mobile retry may legitimately repeat ``init_upload`` after a timeout,
        but the same operation key must never become an alias for another local
        file. Returning the old presigned/multipart contract in that situation
        could upload bytes into the wrong Media record and corrupt resume state.
        """

        try:
            existing_duration = float(getattr(doc, "duration_seconds", 0) or 0)
        except (TypeError, ValueError):
            existing_duration = -1.0
        expected_duration = float(duration_seconds or 0)
        duration_matches = abs(existing_duration - expected_duration) <= 0.001

        matches = all(
            (
                str(getattr(doc, "original_filename", "") or "") == str(filename),
                normalize_content_type(getattr(doc, "content_type", ""))
                == normalize_content_type(content_type),
                int(getattr(doc, "expected_size_bytes", 0) or 0) == int(size_bytes),
                duration_matches,
                str(getattr(doc, "expected_checksum", "") or "").strip().lower()
                == str(checksum_sha256 or "").strip().lower(),
                str(getattr(doc, "upload_mode", "") or "direct").strip().lower()
                == str(upload_mode or "direct").strip().lower(),
            )
        )
        if not matches:
            raise MediaConflictError(
                "Idempotency key was already used for a different upload",
                code="IDEMPOTENCY_CONFLICT",
            )

    @staticmethod
    def _hash_idempotency_key(user: str, purpose: str, key: str | None) -> str:
        clean = str(key or "").strip()
        if not clean:
            return ""
        if len(clean) > 128 or "\x00" in clean:
            raise MediaValidationError("Idempotency key is invalid", code="VALIDATION_ERROR")
        payload = f"{user}\n{purpose}\n{clean}".encode()
        return hashlib.sha256(payload).hexdigest()

    def _enforce_attachment_count(
        self,
        *,
        policy: MediaPurpose,
        attached_doctype: str,
        attached_name: str,
        excluding_media_ids: set[str],
    ) -> None:
        if policy.max_items_per_resource <= 0:
            return
        media = frappe.qb.DocType("AOS Media Object")
        condition = (
            (media.purpose == policy.key)
            & (media.status == "Attached")
            & (media.attached_doctype == attached_doctype)
            & (media.attached_name == attached_name)
        )
        excluded = sorted({item for item in excluding_media_ids if item})
        if excluded:
            condition &= media.name.notin(excluded)
        count = (
            frappe.qb.from_(media)
            .select(Count("*"))
            .where(condition)
            .run()[0][0]
        )
        if int(count or 0) >= policy.max_items_per_resource:
            raise MediaConflictError(
                "Media limit for this resource has been reached",
                code="MEDIA_LIMIT_EXCEEDED",
            )

    @staticmethod
    def _lock_attachment_target(
        *,
        policy: MediaPurpose,
        attached_doctype: str,
        attached_name: str,
    ) -> None:
        doctype = str(attached_doctype or "").strip()
        docname = str(attached_name or "").strip()
        if not doctype or not docname or doctype not in policy.allowed_attachment_doctypes:
            raise MediaValidationError("Invalid media attachment target", code="INVALID_STATE")
        # `doctype` comes exclusively from the immutable purpose allow-list, not
        # from arbitrary client input. Locking the target row makes resource-level
        # cardinality checks atomic across horizontally scaled workers.
        frappe.db.sql(
            f"SELECT name FROM `tab{doctype}` WHERE name = %s FOR UPDATE",
            docname,
        )

    @staticmethod
    def _lock_media_row(media_id: str) -> None:
        clean_id = str(media_id or "").strip()
        if not clean_id:
            return
        frappe.db.sql(
            "SELECT name FROM `tabAOS Media Object` WHERE name = %s FOR UPDATE",
            clean_id,
        )

    def generate_object_key(self, *, owner_user: str, purpose_rule: MediaPurpose, filename: str) -> str:
        extension = os.path.splitext(filename)[1].lower()
        if extension not in purpose_rule.allowed_extensions:
            raise MediaValidationError("File extension is not allowed", code="UNSUPPORTED_MEDIA_TYPE")
        user_hash = hashlib.sha256(str(owner_user or "").encode("utf-8")).hexdigest()[:16]
        now = datetime.utcnow()
        return (
            f"{purpose_rule.prefix}/{now.year:04d}/{now.month:02d}/"
            f"{user_hash}/{uuid.uuid4().hex}{extension}"
        )

    def generate_staging_object_key(
        self,
        *,
        owner_user: str,
        purpose_rule: MediaPurpose,
        filename: str,
    ) -> str:
        extension = os.path.splitext(filename)[1].lower()
        user_hash = hashlib.sha256(str(owner_user or "").encode("utf-8")).hexdigest()[:16]
        now = datetime.utcnow()
        return (
            f"incoming/{purpose_rule.key}/{now.year:04d}/{now.month:02d}/"
            f"{user_hash}/{uuid.uuid4().hex}{extension}"
        )


class _TransientMedia:
    def __init__(self, **kwargs: Any):
        for key, value in kwargs.items():
            setattr(self, key, value)


class _ValidatedObject:
    def __init__(
        self,
        *,
        content_type: str,
        size_bytes: int,
        source_size_bytes: int,
        checksum_sha256: str,
        width: int | None,
        height: int | None,
        duration_seconds: float | None = None,
        finalized_bytes: bytes | None = None,
    ):
        self.content_type = content_type
        self.size_bytes = size_bytes
        self.source_size_bytes = source_size_bytes
        self.checksum_sha256 = checksum_sha256
        self.width = width
        self.height = height
        self.duration_seconds = duration_seconds
        self.finalized_bytes = finalized_bytes


def serialize_media_doc(doc, *, url: str | None = None, include_private_fields: bool = False) -> dict:
    verification_evidence = getattr(doc, "purpose", None) == "verification_document"
    data = {
        "id": doc.name,
        "media_id": doc.name,
        "purpose": doc.purpose,
        "status": doc.status,
        "visibility": doc.visibility,
        "upload_mode": str(getattr(doc, "upload_mode", "") or "direct"),
        "original_filename": None if verification_evidence else doc.original_filename,
        "content_type": doc.content_type,
        "size_bytes": int(doc.size_bytes or 0),
        "checksum_sha256": (
            None
            if verification_evidence
            else str(getattr(doc, "checksum", "") or "") or None
        ),
        "width": int(doc.width or 0) if doc.width else None,
        "height": int(doc.height or 0) if doc.height else None,
        "duration_seconds": float(doc.duration_seconds or 0) if doc.duration_seconds else None,
        "url": url,
        "attached": bool(getattr(doc, "attached_doctype", "") and getattr(doc, "attached_name", "")),
    }
    if include_private_fields and not verification_evidence:
        data.update(
            {
                "bucket": doc.bucket,
                "object_key": doc.object_key,
                "etag": doc.etag,
                "attached_doctype": doc.attached_doctype,
                "attached_name": doc.attached_name,
                "attached_field": doc.attached_field,
            }
        )
    return data
