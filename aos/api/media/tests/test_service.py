from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.services.media.media_service import (
    MediaConflictError,
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaStorageError,
    MediaValidationError,
)
from aos.services.storage.base import (
    ObjectStat,
    StorageNotFoundError,
    StorageUnavailableError,
)
from aos.tests.feature_test_helpers import AOSFeatureTestMixin

PNG_64 = (Path(__file__).parent / "fixtures" / "valid_64x64.png").read_bytes()


@dataclass
class _Stored:
    payload: bytes
    content_type: str
    etag: str


class FakeStorage:
    def __init__(self):
        self.objects: dict[tuple[str, str], _Stored] = {}
        self.copy_calls = 0
        self.delete_calls = 0
        self.fail_delete = False
        self.fail_stat = False

    def bucket_for_type(self, bucket_type: str) -> str:
        return {"public": "aos-public", "private": "aos-private"}[bucket_type]

    def ensure_bucket(self, bucket: str, *, public_read: bool = False) -> None:
        return None

    def presigned_put_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        return f"https://upload.example.test/{bucket}/{object_key}?expires={expiry_minutes}"

    def presigned_get_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        return f"https://download.example.test/{bucket}/{object_key}?expires={expiry_minutes}"

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat:
        if self.fail_stat:
            raise StorageUnavailableError("temporary")
        stored = self.objects.get((bucket, object_key))
        if not stored:
            raise StorageNotFoundError("missing")
        return ObjectStat(
            bucket=bucket,
            object_key=object_key,
            size=len(stored.payload),
            etag=stored.etag,
            content_type=stored.content_type,
        )

    def object_exists(self, bucket: str, object_key: str) -> bool:
        return (bucket, object_key) in self.objects

    def get_range(self, bucket: str, object_key: str, *, offset: int = 0, length: int = 65536) -> bytes:
        stored = self.objects.get((bucket, object_key))
        if not stored:
            raise StorageNotFoundError("missing")
        return stored.payload[offset : offset + length]

    def iter_chunks(self, bucket: str, object_key: str, *, chunk_size: int = 1024 * 1024):
        stored = self.objects.get((bucket, object_key))
        if not stored:
            raise StorageNotFoundError("missing")
        for offset in range(0, len(stored.payload), chunk_size):
            yield stored.payload[offset : offset + chunk_size]

    def get_bytes(self, bucket: str, object_key: str, *, max_bytes: int | None = None) -> bytes:
        stored = self.objects.get((bucket, object_key))
        if not stored:
            raise StorageNotFoundError("missing")
        if max_bytes is not None and len(stored.payload) > max_bytes:
            raise ValueError("too large")
        return stored.payload

    def put_bytes(self, *, bucket: str, object_key: str, data: bytes, content_type: str) -> ObjectStat:
        payload = bytes(data)
        self.objects[(bucket, object_key)] = _Stored(payload, content_type, hashlib.sha256(payload).hexdigest())
        return self.stat_object(bucket, object_key)

    def put_stream(self, *, bucket: str, object_key: str, stream, length: int, content_type: str):
        return self.put_bytes(
            bucket=bucket,
            object_key=object_key,
            data=stream.read(length),
            content_type=content_type,
        )

    def copy_object(
        self,
        *,
        source_bucket: str,
        source_object_key: str,
        destination_bucket: str,
        destination_object_key: str,
    ) -> ObjectStat:
        stored = self.objects.get((source_bucket, source_object_key))
        if not stored:
            raise StorageNotFoundError("missing")
        self.copy_calls += 1
        self.objects[(destination_bucket, destination_object_key)] = stored
        return self.stat_object(destination_bucket, destination_object_key)

    def delete_object(self, bucket: str, object_key: str) -> None:
        self.delete_calls += 1
        if self.fail_delete:
            raise StorageUnavailableError("temporary")
        self.objects.pop((bucket, object_key), None)

    def build_public_url(self, bucket: str, object_key: str) -> str:
        return f"https://files.example.test/{bucket}/{object_key}"

    def healthcheck(self) -> dict[str, object]:
        return {"ok": True, "missing_bucket_count": 0}


class TestMediaService(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("media-service")
        self.created_users: list[str] = []
        self.user = self.make_user("owner", with_preference=False)
        self.other_user = self.make_user("other", with_preference=False)
        self.storage = FakeStorage()
        self.service = MediaService(storage=self.storage)
        frappe.set_user("Administrator")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _init_png(self, *, checksum: str | None = None, idempotency_key: str | None = None):
        return self.service.init_upload(
            user=self.user,
            purpose="profile_image",
            filename="avatar.png",
            content_type="image/png",
            size_bytes=len(PNG_64),
            checksum_sha256=checksum,
            idempotency_key=idempotency_key,
        )[0]

    def _upload_staging(self, doc, payload: bytes = PNG_64) -> None:
        self.storage.put_bytes(
            bucket=doc.upload_bucket,
            object_key=doc.upload_object_key,
            data=payload,
            content_type=doc.content_type,
        )

    def test_service_construction_does_not_require_storage_configuration(self):
        with patch(
            "aos.services.media.media_service.MinioStorage",
            side_effect=RuntimeError("storage configuration should be lazy"),
        ) as storage_factory:
            service = MediaService()
            storage_factory.assert_not_called()

            with self.assertRaisesRegex(RuntimeError, "storage configuration should be lazy"):
                _ = service.storage

            storage_factory.assert_called_once_with()

    def test_init_confirm_and_duplicate_completion_are_idempotent(self):
        doc = self._init_png(checksum=hashlib.sha256(PNG_64).hexdigest())
        self.assertEqual(doc.status, "Initialized")
        self.assertEqual(doc.upload_bucket, "aos-private")
        self.assertEqual(doc.bucket, "aos-public")

        self._upload_staging(doc)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)
        duplicate = self.service.confirm_upload(user=self.user, media_id=doc.name)

        self.assertEqual(confirmed.status, "Uploaded")
        self.assertEqual(duplicate.name, confirmed.name)
        self.assertEqual(self.storage.copy_calls, 1)
        self.assertTrue(self.storage.object_exists(confirmed.bucket, confirmed.object_key))
        self.assertEqual(confirmed.checksum, hashlib.sha256(PNG_64).hexdigest())
        self.assertEqual((confirmed.width, confirmed.height), (64, 64))

    def test_init_idempotency_reuses_one_initialized_media_record(self):
        first = self._init_png(idempotency_key="same-upload-request")
        second = self._init_png(idempotency_key="same-upload-request")

        self.assertEqual(first.name, second.name)
        self.assertEqual(
            frappe.db.count(
                "AOS Media Object",
                {
                    "owner_user": self.user,
                    "purpose": "profile_image",
                    "status": "Initialized",
                    "idempotency_key_hash": first.idempotency_key_hash,
                },
            ),
            1,
        )

    def test_init_idempotency_serializes_final_check_and_insert_on_user_row(self):
        import inspect

        source = inspect.getsource(MediaService.init_upload)
        self.assertIn("tabUser", source)
        self.assertIn("FOR UPDATE", source)
        self.assertGreaterEqual(source.count("_find_reusable_initiated_upload"), 2)

    def test_confirm_rejects_missing_object(self):
        doc = self._init_png()
        with self.assertRaises(MediaNotFoundError) as exc:
            self.service.confirm_upload(user=self.user, media_id=doc.name)
        self.assertEqual(getattr(exc.exception, "code", ""), "UPLOAD_INCOMPLETE")

    def test_confirm_rejects_size_mismatch_and_marks_failed(self):
        doc = self._init_png()
        self._upload_staging(doc, PNG_64 + b"extra")
        with self.assertRaises(MediaValidationError) as exc:
            self.service.confirm_upload(user=self.user, media_id=doc.name)
        self.assertEqual(exc.exception.code, "SIZE_MISMATCH")
        doc.reload()
        self.assertEqual(doc.status, "Failed")

    def test_confirm_rejects_checksum_mismatch(self):
        doc = self._init_png(checksum="0" * 64)
        self._upload_staging(doc)
        with self.assertRaises(MediaValidationError) as exc:
            self.service.confirm_upload(user=self.user, media_id=doc.name)
        self.assertEqual(exc.exception.code, "CHECKSUM_MISMATCH")

    def test_cross_user_cannot_complete_or_delete_media(self):
        doc = self._init_png()
        self._upload_staging(doc)
        with self.assertRaises(MediaPermissionError):
            self.service.confirm_upload(user=self.other_user, media_id=doc.name)
        with self.assertRaises(MediaPermissionError):
            self.service.delete_media(media_id=doc.name, user=self.other_user)

    def test_private_media_requires_authorized_signed_access(self):
        pdf = b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\nendobj\n%%EOF"
        doc = self.service.init_upload(
            user=self.user,
            purpose="verification_document",
            filename="identity.pdf",
            content_type="application/pdf",
            size_bytes=len(pdf),
        )[0]
        self._upload_staging(doc, pdf)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)
        self.assertEqual(confirmed.visibility, "Private")
        self.assertFalse(confirmed.public_url)
        self.assertIn(
            "download.example.test",
            self.service.get_url(media_id=confirmed.name, user=self.user),
        )
        with self.assertRaises(MediaPermissionError):
            self.service.get_url(media_id=confirmed.name, user=None)

    def test_attaching_to_another_users_resource_is_denied(self):
        doc = self._init_png()
        self._upload_staging(doc)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)
        with self.assertRaises(MediaPermissionError):
            self.service.attach_media(
                media_id=confirmed.name,
                user=self.user,
                purpose="profile_image",
                attached_doctype="AOS Profile",
                attached_name=self.other_user,
                attached_field="profile_image_media",
            )

    def test_attached_media_cannot_be_deleted(self):
        doc = self._init_png()
        self._upload_staging(doc)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)
        self.service.attach_media(
            media_id=confirmed.name,
            user=self.user,
            purpose="profile_image",
            attached_doctype="AOS Profile",
            attached_name=self.user,
            attached_field="profile_image_media",
        )
        with self.assertRaises(MediaConflictError):
            self.service.delete_media(media_id=confirmed.name, user=self.user)

    def test_system_cleanup_can_delete_legacy_media_with_policy_drift(self):
        doc = self._init_png()
        self._upload_staging(doc)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)

        # Model a historical row created before the current purpose/MIME policy.
        frappe.db.set_value(
            "AOS Media Object",
            confirmed.name,
            "content_type",
            "video/mp4",
            update_modified=False,
        )

        deleted = self.service.delete_media_as_system(media_id=confirmed.name)

        self.assertEqual(deleted.status, "Deleted")
        self.assertFalse(self.storage.object_exists(confirmed.bucket, confirmed.object_key))
        stored = frappe.db.get_value(
            "AOS Media Object",
            confirmed.name,
            ["status", "deleted_at"],
            as_dict=True,
        )
        self.assertEqual(stored.status, "Deleted")
        self.assertTrue(stored.deleted_at)

    def test_storage_delete_failure_remains_retriable_and_second_delete_succeeds(self):
        doc = self._init_png()
        self._upload_staging(doc)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)

        self.storage.fail_delete = True
        with self.assertRaises(MediaStorageError):
            self.service.delete_media(media_id=confirmed.name, user=self.user)
        confirmed.reload()
        self.assertEqual(confirmed.status, "Delete Pending")
        self.assertGreaterEqual(int(confirmed.retry_count or 0), 1)

        self.storage.fail_delete = False
        deleted = self.service.delete_media(media_id=confirmed.name, user=self.user)
        self.assertEqual(deleted.status, "Deleted")
        duplicate = self.service.delete_media(media_id=confirmed.name, user=self.user)
        self.assertEqual(duplicate.status, "Deleted")

    def test_expired_upload_is_rejected_and_staging_is_marked_for_cleanup(self):
        doc = self._init_png()
        self._upload_staging(doc)
        doc.upload_expires_at = frappe.utils.now_datetime() - timedelta(minutes=1)
        doc.save(ignore_permissions=True)
        with self.assertRaises(MediaValidationError) as exc:
            self.service.confirm_upload(user=self.user, media_id=doc.name)
        self.assertEqual(exc.exception.code, "UPLOAD_EXPIRED")
        doc.reload()
        self.assertEqual(doc.status, "Failed")
        self.assertEqual(int(doc.staging_cleanup_required or 0), 1)
