"""Business-level media service for MinIO-backed AOS media."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import uuid
from dataclasses import asdict
from datetime import datetime

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.services.media.media_purposes import MediaPurpose, get_media_purpose
from aos.services.storage.minio_storage import MinioStorage, ObjectStat
from aos.utils.aos_settings import get_aos_settings_snapshot


class MediaValidationError(ValueError):
    """Raised when media input violates AOS media rules."""


class MediaPermissionError(PermissionError):
    """Raised when current user cannot access or mutate media."""


class MediaNotFoundError(FileNotFoundError):
    """Raised when a media metadata record or object is missing."""


class MediaService:
    """Coordinates AOS Media Object records and MinIO storage."""

    def __init__(self, storage: MinioStorage | None = None):
        self.storage = storage or MinioStorage()

    # INIT / CONFIRM
    def init_upload(
        self,
        *,
        user: str,
        purpose: str,
        filename: str,
        content_type: str,
        size_bytes: int,
    ) -> tuple[object, str, dict[str, str], int]:
        purpose_rule = self._get_purpose_or_raise(purpose)
        filename = self._normalize_filename(filename)
        content_type = self._normalize_content_type(content_type, filename)
        size_bytes = self._normalize_size(size_bytes)

        self._validate_upload_request(
            purpose_rule=purpose_rule,
            content_type=content_type,
            size_bytes=size_bytes,
        )

        bucket = self.storage.bucket_for_type(purpose_rule.bucket_type)
        self.storage.ensure_bucket(bucket, public_read=purpose_rule.is_public)

        object_key = self.generate_object_key(
            owner_user=user,
            purpose_rule=purpose_rule,
            filename=filename,
        )

        public_url = (
            self.storage.build_public_url(bucket, object_key)
            if purpose_rule.is_public
            else ""
        )

        doc = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": user,
                "bucket": bucket,
                "object_key": object_key,
                "original_filename": filename,
                "content_type": content_type,
                "size_bytes": size_bytes,
                "visibility": purpose_rule.visibility,
                "purpose": purpose_rule.key,
                "status": "Initialized",
                "public_url": public_url,
            }
        )
        doc.insert(ignore_permissions=True)

        expiry_minutes = self.get_upload_expiry_minutes()
        upload_url = self.storage.presigned_put_url(
            bucket,
            object_key,
            expiry_minutes=expiry_minutes,
        )

        return doc, upload_url, {"Content-Type": content_type}, expiry_minutes * 60

    def confirm_upload(self, *, user: str, media_id: str) -> object:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)

        if doc.status == "Deleted":
            raise MediaValidationError("Media has been deleted")
        if doc.status not in {"Initialized", "Uploaded"}:
            raise MediaValidationError("Media cannot be confirmed in its current state")

        purpose_rule = self._get_purpose_or_raise(doc.purpose)

        try:
            stat = self.storage.stat_object(doc.bucket, doc.object_key)
        except FileNotFoundError as exc:
            raise MediaNotFoundError("Uploaded object was not found") from exc

        self._validate_confirmed_object(doc=doc, purpose_rule=purpose_rule, stat=stat)

        doc.size_bytes = stat.size or doc.size_bytes or 0
        doc.etag = stat.etag or doc.etag
        if stat.content_type:
            doc.content_type = str(stat.content_type).lower()
        doc.status = "Uploaded"
        doc.uploaded_at = now_datetime()
        if doc.visibility == "Public" and not doc.public_url:
            doc.public_url = self.storage.build_public_url(doc.bucket, doc.object_key)
        doc.save(ignore_permissions=True)

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
    ) -> object:
        """Create an uploaded media object from bytes already held by the backend.

        This is for server-side media transformations such as background removal,
        thumbnails, and future processors. User-facing uploads should continue to
        use init_upload + direct-to-MinIO + confirm_upload.
        """
        purpose_rule = self._get_purpose_or_raise(purpose)
        filename = self._normalize_filename(filename)
        content_type = self._normalize_content_type(content_type, filename)
        payload = bytes(data or b"")
        size_bytes = len(payload)

        self._validate_upload_request(
            purpose_rule=purpose_rule,
            content_type=content_type,
            size_bytes=size_bytes,
        )

        bucket = self.storage.bucket_for_type(purpose_rule.bucket_type)
        self.storage.ensure_bucket(bucket, public_read=purpose_rule.is_public)

        object_key = self.generate_object_key(
            owner_user=user,
            purpose_rule=purpose_rule,
            filename=filename,
        )

        stat = self.storage.put_bytes(
            bucket=bucket,
            object_key=object_key,
            data=payload,
            content_type=content_type,
        )

        public_url = (
            self.storage.build_public_url(bucket, object_key)
            if purpose_rule.is_public
            else ""
        )

        doc = frappe.get_doc(
            {
                "doctype": "AOS Media Object",
                "owner_user": user,
                "bucket": bucket,
                "object_key": object_key,
                "original_filename": filename,
                "content_type": content_type,
                "size_bytes": int(stat.size or size_bytes),
                "etag": stat.etag or "",
                "visibility": purpose_rule.visibility,
                "purpose": purpose_rule.key,
                "status": "Uploaded",
                "public_url": public_url,
                "uploaded_at": now_datetime(),
                "width": int(width or 0) if width else None,
                "height": int(height or 0) if height else None,
                "duration_seconds": float(duration_seconds or 0) if duration_seconds else None,
            }
        )
        doc.insert(ignore_permissions=True)
        return doc

    # GET / URLS
    def get_media_doc(self, media_id: str):
        media_id = str(media_id or "").strip()
        if not media_id:
            raise MediaValidationError("Media id is required")
        if not frappe.db.exists("AOS Media Object", media_id):
            raise MediaNotFoundError("Media not found")
        return frappe.get_doc("AOS Media Object", media_id)

    def get_url(self, *, media_id: str, user: str | None = None, expiry_minutes: int | None = None) -> str:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_read(doc, user)

        if doc.status == "Deleted":
            raise MediaNotFoundError("Media not found")

        if doc.visibility == "Public":
            if doc.public_url:
                return doc.public_url
            return self.storage.build_public_url(doc.bucket, doc.object_key)

        minutes = self._normalize_expiry(expiry_minutes)
        return self.storage.presigned_get_url(doc.bucket, doc.object_key, expiry_minutes=minutes)

    # ATTACH / DETACH
    def assert_media_ready_for_attach(self, *, media_id: str, user: str, purpose: str) -> object:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)

        expected = self._get_purpose_or_raise(purpose)
        if doc.purpose != expected.key:
            raise MediaValidationError("Media has the wrong purpose")
        if doc.status != "Uploaded":
            raise MediaValidationError("Media must be uploaded before it can be attached")
        if doc.attached_doctype or doc.attached_name:
            raise MediaValidationError("Media is already attached")

        return doc

    def attach_media(
        self,
        *,
        media_id: str,
        user: str,
        purpose: str,
        attached_doctype: str,
        attached_name: str,
        attached_field: str | None = None,
    ) -> object:
        doc = self.assert_media_ready_for_attach(
            media_id=media_id,
            user=user,
            purpose=purpose,
        )

        doc.status = "Attached"
        doc.attached_doctype = str(attached_doctype or "").strip()
        doc.attached_name = str(attached_name or "").strip()
        doc.attached_field = str(attached_field or "").strip()
        doc.attached_at = now_datetime()
        doc.save(ignore_permissions=True)
        return doc

    # DELETE
    def delete_media(self, *, media_id: str, user: str, force: bool = False) -> object:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)

        if doc.status == "Deleted":
            return doc

        if doc.status == "Attached" and not force:
            raise MediaValidationError("Attached media cannot be deleted directly")

        self._delete_object_best_effort(doc.bucket, doc.object_key)

        doc.status = "Deleted"
        doc.deleted_at = now_datetime()
        doc.save(ignore_permissions=True)
        return doc

    def mark_delete_pending(self, *, media_id: str, user: str) -> object:
        doc = self.get_media_doc(media_id)
        self.assert_user_can_manage(doc, user)
        if doc.status != "Deleted":
            doc.status = "Delete Pending"
            doc.save(ignore_permissions=True)
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
                "status": "Uploaded",
                "creation": ["<", cutoff],
                "attached_doctype": ["in", ["", None]],
                "attached_name": ["in", ["", None]],
            },
            limit=limit,
        )

    def cleanup_delete_pending(self, *, older_than_hours: int = 24, limit: int = 100) -> int:
        cutoff = add_to_date(now_datetime(), hours=-max(1, int(older_than_hours)))
        return self._cleanup_by_filters(
            filters={"status": "Delete Pending", "modified": ["<", cutoff]},
            limit=limit,
        )

    def _cleanup_by_filters(self, *, filters: dict, limit: int) -> int:
        if not frappe.db.exists("DocType", "AOS Media Object"):
            return 0

        rows = frappe.get_all(
            "AOS Media Object",
            filters=filters,
            fields=["name", "bucket", "object_key"],
            limit_page_length=max(1, min(int(limit or 100), 500)),
            order_by="creation asc",
        )

        cleaned = 0
        for row in rows:
            try:
                self._delete_object_best_effort(row.bucket, row.object_key)
                frappe.db.set_value(
                    "AOS Media Object",
                    row.name,
                    {
                        "status": "Deleted",
                        "deleted_at": now_datetime(),
                    },
                    update_modified=True,
                )
                cleaned += 1
            except Exception:
                frappe.log_error(frappe.get_traceback(), "AOS Media Cleanup Row Failed")

        if cleaned:
            frappe.db.commit()

        return cleaned

    # PERMISSIONS
    def assert_user_can_manage(self, doc, user: str) -> None:
        user = str(user or "").strip()
        if not user or user == "Guest":
            raise MediaPermissionError("Please login to continue")
        if doc.owner_user == user:
            return
        if "System Manager" in frappe.get_roles(user):
            return
        raise MediaPermissionError("You cannot manage this media")

    def assert_user_can_read(self, doc, user: str | None) -> None:
        if doc.visibility == "Public":
            return
        user = str(user or "").strip()
        if not user or user == "Guest":
            raise MediaPermissionError("Please login to continue")
        if doc.owner_user == user:
            return
        if "System Manager" in frappe.get_roles(user):
            return
        if self._user_can_read_chat_attachment(doc, user):
            return
        raise MediaPermissionError("You cannot access this media")

    def _user_can_read_chat_attachment(self, doc, user: str) -> bool:
        """Allow chat participants to read private media attached to visible messages."""
        if getattr(doc, "purpose", None) != "chat_attachment":
            return False

        try:
            rows = frappe.db.sql(
                """
                SELECT a.name
                FROM `tabAOS Message Attachment` a
                INNER JOIN `tabAOS Message` m
                    ON m.name = a.message
                INNER JOIN `tabAOS Conversation` c
                    ON c.name = m.conversation
                WHERE
                    a.media = %(media)s
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
            frappe.log_error(
                frappe.get_traceback(),
                "AOS Media Chat Attachment Permission Check Failed",
            )
            return False

    # INTERNAL HELPERS
    def _get_purpose_or_raise(self, purpose: str) -> MediaPurpose:
        purpose_rule = get_media_purpose(purpose)
        if not purpose_rule:
            raise MediaValidationError("Invalid media purpose")
        return purpose_rule

    def _normalize_filename(self, filename: str) -> str:
        value = os.path.basename(str(filename or "").strip())
        if not value:
            raise MediaValidationError("Filename is required")
        if value in {".", ".."} or "\x00" in value:
            raise MediaValidationError("Invalid filename")
        return value[:140]

    def _normalize_content_type(self, content_type: str, filename: str) -> str:
        value = str(content_type or "").strip().lower()
        if not value:
            guessed = mimetypes.guess_type(filename)[0]
            value = str(guessed or "").strip().lower()
        if not value:
            raise MediaValidationError("Content type is required")
        return value

    def _normalize_size(self, size_bytes: object) -> int:
        try:
            size = int(size_bytes)
        except Exception:
            raise MediaValidationError("File size is required")
        if size <= 0:
            raise MediaValidationError("File size must be greater than zero")
        return size

    def _validate_upload_request(
        self,
        *,
        purpose_rule: MediaPurpose,
        content_type: str,
        size_bytes: int,
    ) -> None:
        if content_type not in purpose_rule.allowed_content_types:
            raise MediaValidationError("Unsupported file type")
        if size_bytes > purpose_rule.max_size_bytes:
            raise MediaValidationError("File is too large")

    def _validate_confirmed_object(self, *, doc, purpose_rule: MediaPurpose, stat: ObjectStat) -> None:
        if stat.size <= 0:
            self._delete_invalid_upload(doc)
            raise MediaValidationError("Uploaded file is empty")
        if stat.size > purpose_rule.max_size_bytes:
            self._delete_invalid_upload(doc)
            raise MediaValidationError("Uploaded file is too large")

        stat_type = str(stat.content_type or "").split(";", 1)[0].strip().lower()
        if stat_type and stat_type != "application/octet-stream" and stat_type not in purpose_rule.allowed_content_types:
            self._delete_invalid_upload(doc)
            raise MediaValidationError("Uploaded file type is not allowed")

    def _delete_invalid_upload(self, doc) -> None:
        self._delete_object_best_effort(doc.bucket, doc.object_key)
        try:
            doc.status = "Deleted"
            doc.deleted_at = now_datetime()
            doc.save(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            pass

    def _delete_object_best_effort(self, bucket: str, object_key: str) -> None:
        try:
            self.storage.delete_object(bucket, object_key)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Media Object Delete Failed")

    def _normalize_expiry(self, expiry_minutes: int | None) -> int:
        try:
            minutes = int(expiry_minutes or self.get_upload_expiry_minutes())
        except Exception:
            minutes = self.get_upload_expiry_minutes()
        return max(1, min(minutes, 60))

    def get_upload_expiry_minutes(self) -> int:
        try:
            settings = get_aos_settings_snapshot()
            return max(1, min(int(settings.media_presigned_upload_expiry_minutes or 10), 60))
        except Exception:
            return 10

    def generate_object_key(self, *, owner_user: str, purpose_rule: MediaPurpose, filename: str) -> str:
        ext = os.path.splitext(filename)[1].lower()
        if not ext:
            guessed_ext = mimetypes.guess_extension(mimetypes.guess_type(filename)[0] or "")
            ext = guessed_ext or ""
        if len(ext) > 12:
            ext = ""

        user_hash = hashlib.sha256(str(owner_user or "").encode("utf-8")).hexdigest()[:16]
        now = datetime.utcnow()
        return f"{purpose_rule.prefix}/{now.year:04d}/{now.month:02d}/{user_hash}/{uuid.uuid4().hex}{ext}"


def serialize_media_doc(doc, *, url: str | None = None, include_private_fields: bool = False) -> dict:
    data = {
        "id": doc.name,
        "purpose": doc.purpose,
        "status": doc.status,
        "visibility": doc.visibility,
        "original_filename": doc.original_filename,
        "content_type": doc.content_type,
        "size_bytes": int(doc.size_bytes or 0),
        "width": int(doc.width or 0) if doc.width else None,
        "height": int(doc.height or 0) if doc.height else None,
        "duration_seconds": float(doc.duration_seconds or 0) if doc.duration_seconds else None,
        "url": url,
    }

    if include_private_fields:
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
