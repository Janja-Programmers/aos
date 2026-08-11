"""Production media application/domain service for AOS-owned object storage."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime
from typing import Any, Iterable

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from aos.services.media.content_validation import (
    CHUNK_SIZE,
    MediaContentValidationError,
    normalize_checksum,
    normalize_content_type,
    normalize_filename,
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
    ObjectStat,
    StorageAdapter,
    StorageConfigurationError,
    StorageNotFoundError,
    StorageUnavailableError,
    StorageValidationError,
)
from aos.services.storage.minio_storage import MinioStorage
from aos.utils.aos_config import get_media_download_expiry_minutes
from aos.utils.aos_settings import get_aos_settings_snapshot

ACTIVE_READABLE_STATUSES = {"Uploaded", "Processing", "Ready", "Attached"}
TERMINAL_STATUSES = {"Failed", "Replaced", "Deleted"}


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
        # and must not require production MinIO credentials merely to construct the
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
            self._storage = MinioStorage()
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
        checksum_sha256: str | None = None,
        idempotency_key: str | None = None,
        system: bool = False,
    ) -> tuple[object, str, dict[str, str], int]:
        policy = self._get_purpose_or_raise(purpose)
        self._assert_purpose_upload_allowed(user=user, policy=policy, system=system)

        clean_filename = self._normalize_filename(filename)
        clean_content_type = self._normalize_content_type(content_type, clean_filename)
        clean_size = self._normalize_size(size_bytes)
        clean_checksum = self._normalize_checksum(checksum_sha256)
        validate_filename_extension(clean_filename, policy.allowed_extensions)
        self._validate_upload_request(
            purpose_rule=policy,
            content_type=clean_content_type,
            size_bytes=clean_size,
        )

        idempotency_hash = self._hash_idempotency_key(user, policy.key, idempotency_key)
        if idempotency_hash:
            existing = self._find_reusable_initiated_upload(
                user=user,
                purpose=policy.key,
                idempotency_hash=idempotency_hash,
            )
            if existing:
                return self._issue_upload_url(existing)

        try:
            final_bucket = self.storage.bucket_for_type(policy.bucket_type)
            staging_bucket = self.storage.bucket_for_type("private")
            self.storage.ensure_bucket(staging_bucket, public_read=False)
            self.storage.ensure_bucket(final_bucket, public_read=policy.is_public)
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc

        final_key = self.generate_object_key(
            owner_user=user,
            purpose_rule=policy,
            filename=clean_filename,
        )
        staging_key = self.generate_staging_object_key(
            owner_user=user,
            purpose_rule=policy,
            filename=clean_filename,
        )
        expires_at = add_to_date(now_datetime(), minutes=self.get_upload_expiry_minutes())

        doc = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": user,
                "bucket": final_bucket,
                "object_key": final_key,
                "upload_bucket": staging_bucket,
                "upload_object_key": staging_key,
                "original_filename": clean_filename,
                "content_type": clean_content_type,
                "expected_size_bytes": clean_size,
                "size_bytes": clean_size,
                "expected_checksum": clean_checksum,
                "visibility": policy.visibility,
                "purpose": policy.key,
                "status": "Initialized",
                "public_url": "",
                "upload_expires_at": expires_at,
                "idempotency_key_hash": idempotency_hash,
                "retry_count": 0,
            }
        )
        doc.insert(ignore_permissions=True)

        try:
            result = self._issue_upload_url(doc)
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

    def _issue_upload_url(self, doc) -> tuple[object, str, dict[str, str], int]:
        expiry_minutes = self.get_upload_expiry_minutes()
        upload_bucket = str(getattr(doc, "upload_bucket", "") or doc.bucket)
        upload_key = str(getattr(doc, "upload_object_key", "") or doc.object_key)
        upload_url = self.storage.presigned_put_url(
            upload_bucket,
            upload_key,
            expiry_minutes=expiry_minutes,
        )
        return doc, upload_url, {"Content-Type": normalize_content_type(doc.content_type)}, expiry_minutes * 60

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
            doc.uploaded_at = now_datetime()
            doc.completed_at = now_datetime()
            doc.failure_code = ""
            doc.failure_reason = ""
            doc.last_storage_error = ""
            doc.public_url = self._public_url_for_doc(doc) if doc.visibility == "Public" else ""
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
                    "public_url": self.storage.build_public_url(bucket, object_key) if policy.is_public else "",
                    "uploaded_at": now_datetime(),
                    "completed_at": now_datetime(),
                    "width": int(actual_width or 0) or None,
                    "height": int(actual_height or 0) or None,
                    "duration_seconds": clean_duration,
                    "derived_from_media": str(derived_from_media or "").strip(),
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
                "etag": etag or stat.etag or "",
                "checksum": validated.checksum_sha256,
                "visibility": policy.visibility,
                "purpose": policy.key,
                "status": "Uploaded",
                "public_url": self.storage.build_public_url(clean_bucket, clean_key) if policy.is_public else "",
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
        self._lock_media_row(media_id)
        doc = self.validate_media_for_use(
            media_id=media_id,
            user=user,
            purpose=purpose,
            attached_doctype=attached_doctype,
            attached_name=attached_name,
        )
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
        return self._delete_media(media_id=media_id, user=user, system=False)

    def delete_media_as_system(self, *, media_id: str) -> object:
        return self._delete_media(media_id=media_id, user="Administrator", system=True)

    def _delete_media(self, *, media_id: str, user: str, system: bool) -> object:
        self._lock_media_row(media_id)
        doc = self.get_media_doc(media_id)
        if not system:
            self.assert_user_can_manage(doc, user)
        if doc.status == "Deleted":
            return doc
        if doc.status == "Attached" or self._has_external_reference(doc.name):
            raise MediaConflictError(
                "Referenced media must be detached or replaced before deletion",
                code="MEDIA_ALREADY_ATTACHED",
            )

        if not system:
            policy = self._get_purpose_or_raise(doc.purpose)
            if not policy.deletion_permitted:
                raise MediaPermissionError("Media deletion is not permitted", code="MEDIA_ACCESS_DENIED")

        self._persist_delete_lifecycle(
            doc,
            {
                "status": "Delete Pending",
                "delete_requested_at": now_datetime(),
            },
            bypass_validation=system,
        )
        media_log("delete_requested", media_id=doc.name, purpose=doc.purpose, operation="delete")

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
        self._persist_delete_lifecycle(
            doc,
            deleted_values,
            bypass_validation=system,
        )
        media_log("delete_completed", media_id=doc.name, purpose=doc.purpose, operation="delete")
        return doc

    def mark_delete_pending(self, *, media_id: str, user: str) -> object:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        if doc.status not in {"Deleted", "Delete Pending"}:
            doc.status = "Delete Pending"
            doc.delete_requested_at = now_datetime()
            doc.save(ignore_permissions=True)
        return doc

    # PROCESSING
    def mark_processing(self, *, media_id: str, user: str | None = None, system: bool = False) -> object:
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
            filters={"status": "Delete Pending", "modified": ["<", cutoff]},
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
            limit_page_length=max(1, min(int(limit or 100), 500)),
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
            limit_page_length=max(1, min(int(limit or 100), 500)),
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
        return False

    # PERMISSIONS
    def assert_user_can_manage(self, doc, user: str) -> None:
        clean_user = str(user or "").strip()
        if not clean_user or clean_user == "Guest":
            raise MediaPermissionError("Authentication is required", code="AUTH_REQUIRED")
        if doc.owner_user == clean_user:
            return
        if "System Manager" in set(frappe.get_roles(clean_user) or []):
            return
        raise MediaPermissionError("You cannot manage this media", code="MEDIA_OWNERSHIP_REQUIRED")

    def assert_user_can_read(self, doc, user: str | None) -> None:
        if doc.visibility == "Public":
            return
        clean_user = str(user or "").strip()
        if not clean_user or clean_user == "Guest":
            raise MediaPermissionError("Authentication is required", code="AUTH_REQUIRED")
        roles = set(frappe.get_roles(clean_user) or [])
        if getattr(doc, "purpose", None) == "verification_document":
            if "System Manager" in roles:
                return
            # Before submission the uploader may preview their confirmed upload.
            # Once attached, both Media ownership and parent-request ownership
            # must still match. Released/orphaned evidence is not readable merely
            # because the account originally uploaded it.
            if doc.owner_user == clean_user and doc.status == "Uploaded" and not doc.attached_name:
                return
            if doc.owner_user == clean_user and self._user_can_read_verification_document(doc, clean_user):
                return
            raise MediaPermissionError("You cannot access this media", code="MEDIA_ACCESS_DENIED")
        if doc.owner_user == clean_user:
            return
        if "System Manager" in roles:
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

        claimed = normalize_content_type(getattr(doc, "content_type", "") or stat.content_type)
        if claimed not in purpose_rule.allowed_content_types:
            raise MediaValidationError("Uploaded file type is not allowed", code="UNSUPPORTED_MEDIA_TYPE")
        try:
            head = self.storage.get_range(bucket, object_key, length=64 * 1024)
            detected = validate_magic_type(
                head=head,
                claimed_content_type=claimed,
                allowed_content_types=purpose_rule.allowed_content_types,
            )

            width = None
            height = None
            if detected.startswith("image/"):
                payload = self.storage.get_bytes(
                    bucket,
                    object_key,
                    max_bytes=purpose_rule.max_size_bytes,
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
                checksum, actual_size = sha256_chunks(
                    self.storage.iter_chunks(bucket, object_key, chunk_size=CHUNK_SIZE),
                    content_type=detected,
                )
        except StorageNotFoundError as exc:
            raise MediaValidationError(
                "Uploaded object is incomplete", code="UPLOAD_INCOMPLETE"
            ) from exc
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
            raise MediaStorageError("Storage is temporarily unavailable") from exc
        if actual_size != stat.size:
            raise MediaValidationError("Uploaded file changed during verification", code="UPLOAD_INCOMPLETE")
        expected_checksum = str(getattr(doc, "expected_checksum", "") or "").strip().lower()
        if expected_checksum and checksum != expected_checksum:
            raise MediaValidationError("Uploaded file checksum does not match", code="CHECKSUM_MISMATCH")

        return _ValidatedObject(
            content_type=detected,
            size_bytes=actual_size,
            checksum_sha256=checksum,
            width=width,
            height=height,
        )

    def _finalize_staged_object(
        self,
        *,
        doc,
        source_bucket: str,
        source_object_key: str,
        expected_size: int,
    ) -> ObjectStat:
        if source_bucket == doc.bucket and source_object_key == doc.object_key:
            return self.storage.stat_object(doc.bucket, doc.object_key)
        try:
            final_stat = self.storage.copy_object(
                source_bucket=source_bucket,
                source_object_key=source_object_key,
                destination_bucket=doc.bucket,
                destination_object_key=doc.object_key,
            )
        except StorageNotFoundError as exc:
            raise MediaValidationError("Staged upload is incomplete", code="UPLOAD_INCOMPLETE") from exc
        except (
            StorageConfigurationError,
            StorageUnavailableError,
            StorageValidationError,
        ) as exc:
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
        """Remove current staging bytes but retain identity until URL expiry.

        A presigned PUT cannot be revoked. Keeping the staging identity and a
        cleanup marker lets reconciliation remove a malicious/accidental late
        re-upload after confirmation.
        """
        upload_bucket, upload_key = self._upload_identity(doc)
        if upload_bucket == doc.bucket and upload_key == doc.object_key:
            return
        try:
            self.storage.delete_object(upload_bucket, upload_key)
        except Exception:
            pass
        frappe.db.set_value(
            "AOS Media Object",
            doc.name,
            "staging_cleanup_required",
            1,
            update_modified=False,
        )
        doc.staging_cleanup_required = 1

    def _delete_staging_best_effort(self, doc) -> None:
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

    def get_upload_expiry_minutes(self) -> int:
        try:
            settings = get_aos_settings_snapshot()
            return max(1, min(int(settings.media_presigned_upload_expiry_minutes or 10), 60))
        except Exception:
            return 10

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
            limit_page_length=1,
        )
        if not rows:
            return None
        doc = frappe.get_doc("AOS Media Object", rows[0].name)
        if self._upload_is_expired(doc):
            return None
        return doc

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
        count = frappe.db.count(
            "AOS Media Object",
            filters={
                "purpose": policy.key,
                "status": "Attached",
                "attached_doctype": attached_doctype,
                "attached_name": attached_name,
                "name": ["not in", [item for item in excluding_media_ids if item]],
            },
        )
        if int(count or 0) >= policy.max_items_per_resource:
            raise MediaConflictError(
                "Media limit for this resource has been reached",
                code="MEDIA_LIMIT_EXCEEDED",
            )

    @staticmethod
    def _lock_media_row(media_id: str) -> None:
        clean_id = str(media_id or "").strip()
        if not clean_id:
            return
        try:
            frappe.db.sql(
                "SELECT name FROM `tabAOS Media Object` WHERE name = %s FOR UPDATE",
                clean_id,
            )
        except Exception:
            # Unit mocks and database engines without row-lock syntax still rely
            # on lifecycle/idempotency checks below.
            pass

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
        checksum_sha256: str,
        width: int | None,
        height: int | None,
    ):
        self.content_type = content_type
        self.size_bytes = size_bytes
        self.checksum_sha256 = checksum_sha256
        self.width = width
        self.height = height


def serialize_media_doc(doc, *, url: str | None = None, include_private_fields: bool = False) -> dict:
    verification_evidence = getattr(doc, "purpose", None) == "verification_document"
    data = {
        "id": doc.name,
        "media_id": doc.name,
        "purpose": doc.purpose,
        "status": doc.status,
        "visibility": doc.visibility,
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
