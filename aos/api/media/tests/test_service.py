from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.query_builder.functions import Count
from aos.services.accounts.identity import profile_name_for_user
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
    MultipartPart,
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
        self.iter_chunk_calls = 0
        self.fail_delete = False
        self.fail_stat = False
        self.multipart_uploads: dict[str, dict[str, object]] = {}
        self.multipart_create_calls = 0
        self.multipart_complete_calls = 0
        self.multipart_abort_calls = 0
        self.complete_then_not_found_once = False

    def bucket_for_type(self, bucket_type: str) -> str:
        return {"public": "aos-public", "private": "aos-private"}[bucket_type]

    def ensure_bucket(self, bucket: str, *, public_read: bool = False) -> None:
        return None

    def presigned_put_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        return f"https://upload.example.test/{bucket}/{object_key}?expires={expiry_minutes}"

    def presigned_get_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        return f"https://download.example.test/{bucket}/{object_key}?expires={expiry_minutes}"

    def create_multipart_upload(self, bucket: str, object_key: str, *, content_type: str) -> str:
        self.multipart_create_calls += 1
        upload_id = f"upload-{uuid.uuid4().hex}"
        self.multipart_uploads[upload_id] = {
            "bucket": bucket,
            "object_key": object_key,
            "content_type": content_type,
            "parts": {},
        }
        return upload_id

    def presigned_upload_part_url(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
        part_number: int,
        expiry_minutes: int,
    ) -> str:
        session = self.multipart_uploads.get(upload_id)
        if not session:
            raise StorageNotFoundError("missing multipart session")
        if session["bucket"] != bucket or session["object_key"] != object_key:
            raise StorageNotFoundError("multipart identity mismatch")
        return (
            f"https://upload.example.test/{bucket}/{object_key}"
            f"?partNumber={part_number}&uploadId={upload_id}&expires={expiry_minutes}"
        )

    def upload_multipart_test_part(self, upload_id: str, part_number: int, payload: bytes) -> None:
        session = self.multipart_uploads[upload_id]
        parts = session["parts"]
        assert isinstance(parts, dict)
        parts[int(part_number)] = bytes(payload)

    def list_multipart_parts(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
    ) -> list[MultipartPart]:
        session = self.multipart_uploads.get(upload_id)
        if not session:
            raise StorageNotFoundError("missing multipart session")
        if session["bucket"] != bucket or session["object_key"] != object_key:
            raise StorageNotFoundError("multipart identity mismatch")
        parts = session["parts"]
        assert isinstance(parts, dict)
        return [
            MultipartPart(
                part_number=number,
                etag=hashlib.sha256(payload).hexdigest(),
                size=len(payload),
            )
            for number, payload in sorted(parts.items())
        ]

    def complete_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
        parts: list[MultipartPart],
    ) -> ObjectStat:
        session = self.multipart_uploads.get(upload_id)
        if not session:
            raise StorageNotFoundError("missing multipart session")
        stored_parts = session["parts"]
        assert isinstance(stored_parts, dict)
        payload = b"".join(stored_parts[part.part_number] for part in parts)
        content_type = str(session["content_type"])
        self.put_bytes(
            bucket=bucket,
            object_key=object_key,
            data=payload,
            content_type=content_type,
        )
        self.multipart_complete_calls += 1
        self.multipart_uploads.pop(upload_id, None)
        if self.complete_then_not_found_once:
            self.complete_then_not_found_once = False
            raise StorageNotFoundError("ambiguous completion response")
        return self.stat_object(bucket, object_key)

    def abort_multipart_upload(self, bucket: str, object_key: str, *, upload_id: str) -> None:
        self.multipart_abort_calls += 1
        self.multipart_uploads.pop(upload_id, None)

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
        self.iter_chunk_calls += 1
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

    @staticmethod
    def _count_media_rows(*conditions) -> int:
        media = frappe.qb.DocType("AOS Media Object")
        query = frappe.qb.from_(media).select(Count("*"))
        for condition in conditions:
            query = query.where(condition)
        return int(query.run()[0][0] or 0)

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
            self._count_media_rows(
                frappe.qb.DocType("AOS Media Object").owner_user == self.user,
                frappe.qb.DocType("AOS Media Object").purpose == "profile_image",
                frappe.qb.DocType("AOS Media Object").status == "Initialized",
                frappe.qb.DocType("AOS Media Object").idempotency_key_hash == first.idempotency_key_hash,
            ),
            1,
        )

    def test_init_idempotency_rejects_reuse_for_different_file_contract(self):
        first = self._init_png(idempotency_key="same-upload-request")

        with self.assertRaises(MediaConflictError) as exc:
            self.service.init_upload(
                user=self.user,
                purpose="profile_image",
                filename="different.png",
                content_type="image/png",
                size_bytes=len(PNG_64),
                idempotency_key="same-upload-request",
            )

        self.assertEqual(exc.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(
            self._count_media_rows(
                frappe.qb.DocType("AOS Media Object").owner_user == self.user,
                frappe.qb.DocType("AOS Media Object").purpose == "profile_image",
                frappe.qb.DocType("AOS Media Object").idempotency_key_hash == first.idempotency_key_hash,
            ),
            1,
        )

    def test_init_idempotency_serializes_final_check_and_insert_on_user_row(self):
        import inspect

        source = inspect.getsource(MediaService.init_upload)
        self.assertIn("tabUser", source)
        self.assertIn("FOR UPDATE", source)
        self.assertGreaterEqual(source.count("_find_reusable_initiated_upload"), 2)

    def test_short_video_requires_duration_before_issuing_large_upload_url(self):
        with self.assertRaises(MediaValidationError) as exc:
            self.service.init_upload(
                user=self.user,
                purpose="short_video_raw",
                filename="short.mp4",
                content_type="video/mp4",
                size_bytes=1024,
            )
        self.assertEqual(exc.exception.code, "DURATION_REQUIRED")

        with self.assertRaises(MediaValidationError):
            self.service.init_upload(
                user=self.user,
                purpose="short_video_raw",
                filename="short.mp4",
                content_type="video/mp4",
                size_bytes=1024,
                duration_seconds=601,
            )

    def test_large_short_auto_uses_resumable_multipart_contract(self):
        size_bytes = (16 * 1024 * 1024) + 1
        doc, upload_url, upload_headers, expires_in = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=size_bytes,
            duration_seconds=540,
            upload_mode="auto",
        )

        self.assertEqual(doc.upload_mode, "multipart")
        self.assertIsNone(upload_url)
        self.assertEqual(upload_headers, {})
        self.assertEqual(int(doc.multipart_part_size_bytes), 8 * 1024 * 1024)
        self.assertEqual(int(doc.multipart_part_count), 3)
        self.assertTrue(doc.multipart_upload_id)
        self.assertEqual(doc.upload_bucket, doc.bucket)
        self.assertEqual(doc.upload_object_key, doc.object_key)
        self.assertEqual(self.storage.multipart_create_calls, 1)
        self.assertGreater(expires_in, 60 * 60)

    def test_large_short_explicit_direct_upload_is_rejected_before_storage_creation(self):
        with self.assertRaises(MediaValidationError) as exc:
            self.service.init_upload(
                user=self.user,
                purpose="short_video_raw",
                filename="long-short.mp4",
                content_type="video/mp4",
                size_bytes=(16 * 1024 * 1024) + 1,
                duration_seconds=540,
                upload_mode="direct",
            )

        self.assertEqual(exc.exception.code, "MULTIPART_REQUIRED")
        self.assertEqual(self.storage.multipart_create_calls, 0)

    def test_large_short_legacy_client_without_upload_mode_is_rejected_before_bytes(self):
        with self.assertRaises(MediaValidationError) as exc:
            self.service.init_upload(
                user=self.user,
                purpose="short_video_raw",
                filename="legacy-long-short.mp4",
                content_type="video/mp4",
                size_bytes=(16 * 1024 * 1024) + 1,
                duration_seconds=540,
            )

        self.assertEqual(exc.exception.code, "MULTIPART_REQUIRED")
        self.assertEqual(self.storage.multipart_create_calls, 0)

    def test_multipart_part_url_batch_is_bounded_and_upload_id_is_not_api_field(self):
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=(24 * 1024 * 1024) + 100,
            duration_seconds=540,
            upload_mode="auto",
        )[0]

        contract = self.service.get_multipart_part_urls(
            user=self.user,
            media_id=doc.name,
            start_part=2,
            count=2,
        )

        self.assertNotIn("upload_id", contract)
        self.assertEqual([part["part_number"] for part in contract["parts"]], [2, 3])
        self.assertEqual(
            [part["expected_size_bytes"] for part in contract["parts"]],
            [8 * 1024 * 1024, 8 * 1024 * 1024],
        )
        self.assertTrue(all("uploadId=" in part["upload_url"] for part in contract["parts"]))

    def test_multipart_status_uses_storage_truth_for_resume(self):
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=(16 * 1024 * 1024) + 100,
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)
        part_size = int(doc.multipart_part_size_bytes)
        self.storage.upload_multipart_test_part(upload_id, 1, b"a" * part_size)
        self.storage.upload_multipart_test_part(upload_id, 2, b"b" * part_size)

        status = self.service.get_multipart_status(user=self.user, media_id=doc.name)

        self.assertEqual(status["uploaded_parts"], [1, 2])
        self.assertEqual(status["missing_parts"], [3])
        self.assertEqual(status["bytes_uploaded"], part_size * 2)
        self.assertFalse(status["complete_ready"])
        self.assertEqual(status["progress"], (part_size * 2) / ((16 * 1024 * 1024) + 100))

    def test_multipart_status_exposes_invalid_parts_as_retry_parts(self):
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=(16 * 1024 * 1024) + 100,
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)
        part_size = int(doc.multipart_part_size_bytes)
        self.storage.upload_multipart_test_part(upload_id, 1, b"a" * part_size)
        self.storage.upload_multipart_test_part(upload_id, 2, b"b" * (part_size - 1))

        status = self.service.get_multipart_status(user=self.user, media_id=doc.name)

        self.assertEqual(status["invalid_parts"], [2])
        self.assertEqual(status["missing_parts"], [3])
        self.assertEqual(status["retry_parts"], [2, 3])
        self.assertFalse(status["complete_ready"])

    def test_multipart_completion_validates_parts_assembles_and_confirms(self):
        payload = b"\x00\x00\x00\x18ftypmp42" + (b"v" * ((16 * 1024 * 1024) + 100))
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=len(payload),
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)
        part_size = int(doc.multipart_part_size_bytes)
        for part_number in range(1, int(doc.multipart_part_count) + 1):
            start = (part_number - 1) * part_size
            end = min(len(payload), start + part_size)
            self.storage.upload_multipart_test_part(upload_id, part_number, payload[start:end])

        confirmed = self.service.complete_multipart_upload(user=self.user, media_id=doc.name)
        duplicate = self.service.complete_multipart_upload(user=self.user, media_id=doc.name)

        self.assertEqual(confirmed.status, "Uploaded")
        self.assertEqual(duplicate.name, confirmed.name)
        self.assertEqual(self.storage.multipart_complete_calls, 1)
        self.assertEqual(self.storage.copy_calls, 0)
        self.assertEqual(self.storage.iter_chunk_calls, 0)
        self.assertTrue(self.storage.object_exists(confirmed.bucket, confirmed.object_key))
        self.assertEqual(confirmed.multipart_upload_id or "", "")
        self.assertEqual(confirmed.upload_bucket or "", "")
        self.assertEqual(confirmed.upload_object_key or "", "")

    def test_multipart_completion_rejects_incomplete_upload_without_assembling(self):
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=(16 * 1024 * 1024) + 100,
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)
        self.storage.upload_multipart_test_part(
            upload_id,
            1,
            b"a" * int(doc.multipart_part_size_bytes),
        )

        with self.assertRaises(MediaConflictError) as exc:
            self.service.complete_multipart_upload(user=self.user, media_id=doc.name)

        self.assertEqual(exc.exception.code, "MULTIPART_INCOMPLETE")
        self.assertEqual(self.storage.multipart_complete_calls, 0)
        doc.reload()
        self.assertEqual(doc.status, "Initialized")

    def test_multipart_completion_marks_lost_storage_session_terminal(self):
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="lost-session-short.mp4",
            content_type="video/mp4",
            size_bytes=(16 * 1024 * 1024) + 100,
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        self.storage.multipart_uploads.pop(str(doc.multipart_upload_id), None)

        with self.assertRaises(MediaConflictError) as exc:
            self.service.complete_multipart_upload(user=self.user, media_id=doc.name)

        self.assertEqual(exc.exception.code, "MULTIPART_SESSION_LOST")
        doc.reload()
        self.assertEqual(doc.status, "Failed")
        self.assertEqual(doc.failure_code, "MULTIPART_SESSION_LOST")

    def test_multipart_abort_is_idempotent_and_marks_terminal_failure(self):
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="long-short.mp4",
            content_type="video/mp4",
            size_bytes=(16 * 1024 * 1024) + 100,
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)

        aborted = self.service.abort_multipart_upload(user=self.user, media_id=doc.name)
        duplicate = self.service.abort_multipart_upload(user=self.user, media_id=doc.name)

        self.assertEqual(aborted.status, "Failed")
        self.assertEqual(aborted.failure_code, "UPLOAD_ABORTED")
        self.assertEqual(duplicate.name, aborted.name)
        self.assertNotIn(upload_id, self.storage.multipart_uploads)

    def test_multipart_complete_heals_when_storage_completed_before_frappe_commit(self):
        payload = b"\x00\x00\x00\x18ftypmp42" + (b"r" * ((16 * 1024 * 1024) + 100))
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="recover-short.mp4",
            content_type="video/mp4",
            size_bytes=len(payload),
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)
        part_size = int(doc.multipart_part_size_bytes)
        for part_number in range(1, int(doc.multipart_part_count) + 1):
            start = (part_number - 1) * part_size
            end = min(len(payload), start + part_size)
            self.storage.upload_multipart_test_part(upload_id, part_number, payload[start:end])
        parts = self.storage.list_multipart_parts(
            doc.upload_bucket,
            doc.upload_object_key,
            upload_id=upload_id,
        )
        self.storage.complete_multipart_upload(
            doc.upload_bucket,
            doc.upload_object_key,
            upload_id=upload_id,
            parts=parts,
        )

        status = self.service.get_multipart_status(user=self.user, media_id=doc.name)
        recovered = self.service.complete_multipart_upload(user=self.user, media_id=doc.name)

        self.assertTrue(status["storage_complete"])
        self.assertTrue(status["complete_ready"])
        self.assertEqual(recovered.status, "Uploaded")

    def test_multipart_completion_heals_ambiguous_storage_completion_response(self):
        payload = b"\x00\x00\x00\x18ftypmp42" + (b"a" * ((16 * 1024 * 1024) + 100))
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="ambiguous-short.mp4",
            content_type="video/mp4",
            size_bytes=len(payload),
            duration_seconds=540,
            upload_mode="auto",
        )[0]
        upload_id = str(doc.multipart_upload_id)
        part_size = int(doc.multipart_part_size_bytes)
        for part_number in range(1, int(doc.multipart_part_count) + 1):
            start = (part_number - 1) * part_size
            end = min(len(payload), start + part_size)
            self.storage.upload_multipart_test_part(upload_id, part_number, payload[start:end])
        self.storage.complete_then_not_found_once = True

        confirmed = self.service.complete_multipart_upload(user=self.user, media_id=doc.name)

        self.assertEqual(confirmed.status, "Uploaded")
        self.assertEqual(self.storage.multipart_complete_calls, 1)

    def test_short_video_without_client_checksum_does_not_restream_whole_object_on_confirm(self):
        payload = b"\x00\x00\x00\x18ftypmp42" + (b"x" * 4096)
        doc = self.service.init_upload(
            user=self.user,
            purpose="short_video_raw",
            filename="short.mp4",
            content_type="video/mp4",
            size_bytes=len(payload),
            duration_seconds=600,
        )[0]
        self.storage.put_bytes(
            bucket=doc.upload_bucket,
            object_key=doc.upload_object_key,
            data=payload,
            content_type="video/mp4",
        )

        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)

        self.assertEqual(confirmed.status, "Uploaded")
        self.assertEqual(float(confirmed.duration_seconds), 600.0)
        self.assertEqual(confirmed.checksum or "", "")
        self.assertEqual(self.storage.iter_chunk_calls, 0)

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
                attached_name=profile_name_for_user(self.other_user),
                attached_field="profile_image_media",
            )

    def test_attached_media_cannot_be_deleted(self):
        doc = self._init_png()
        self._upload_staging(doc)
        confirmed = self.service.confirm_upload(user=self.user, media_id=doc.name)
        profile_name = profile_name_for_user(self.user)
        self.assertTrue(profile_name)
        self.service.attach_media(
            media_id=confirmed.name,
            user=self.user,
            purpose="profile_image",
            attached_doctype="AOS Profile",
            attached_name=profile_name,
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
