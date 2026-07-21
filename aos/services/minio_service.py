"""Backward-compatible Shorts storage facade.

New media code must depend on ``StorageAdapter``/``MediaService``. This class
preserves the historical processed-Shorts API while delegating all MinIO I/O to
the single hardened adapter.
"""

from __future__ import annotations

import mimetypes
import os
import uuid

from aos.services.storage.minio_storage import MinioStorage
from aos.utils.aos_config import get_minio_config
from aos.utils.aos_settings import get_aos_settings_snapshot


class MinioService:
    """Compatibility facade for legacy processed Shorts object keys."""

    def __init__(self, storage: MinioStorage | None = None):
        self.config = get_minio_config()
        self.storage = storage or MinioStorage(self.config)
        self.bucket = str(self.config.bucket or "").strip()
        self.base_path = str(self.config.base_path or "shorts").strip("/")
        try:
            settings = get_aos_settings_snapshot()
            expiry = int(settings.media_presigned_upload_expiry_minutes or 10)
        except Exception:
            expiry = 10
        self.upload_expiry_minutes = max(1, min(expiry, 60))
        if not self.bucket:
            raise ValueError("AOS_MINIO_BUCKET is required")

    def generate_file_key(self, folder: str = "raw", filename: str = "video.mp4") -> str:
        clean_folder = str(folder or "raw").strip("/")
        extension = os.path.splitext(str(filename or "video.mp4"))[1].lower() or ".mp4"
        prefix = f"{self.base_path}/" if self.base_path else ""
        return f"{prefix}{clean_folder}/{uuid.uuid4().hex}{extension}"

    def _object_name(self, file_key: str) -> str:
        clean = str(file_key or "").strip().strip("/")
        if not clean:
            raise ValueError("File key is required")
        # Preserve the historical object layout used by existing Shorts rows.
        if self.base_path and clean.startswith(f"{self.base_path}/"):
            return clean.removeprefix(f"{self.base_path}/")
        return clean

    def get_presigned_upload_url(self, file_key: str) -> str:
        return self.storage.presigned_put_url(
            self.bucket,
            self._object_name(file_key),
            expiry_minutes=self.upload_expiry_minutes,
        )

    def get_presigned_download_url(self, file_key: str, *, expiry_minutes: int | None = None) -> str:
        return self.storage.presigned_get_url(
            self.bucket,
            self._object_name(file_key),
            expiry_minutes=max(1, min(int(expiry_minutes or self.upload_expiry_minutes), 60)),
        )

    def get_public_url(self, file_key: str) -> str:
        return self.storage.build_public_url(self.bucket, self._object_name(file_key))

    def file_exists(self, file_key: str) -> bool:
        return self.storage.object_exists(self.bucket, self._object_name(file_key))

    def upload_file(self, file_key: str, local_path: str, content_type: str | None = None):
        if not os.path.isfile(local_path):
            raise ValueError("Local file does not exist")
        detected = content_type or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
        with open(local_path, "rb") as stream:
            return self.storage.put_stream(
                bucket=self.bucket,
                object_key=self._object_name(file_key),
                stream=stream,
                length=os.path.getsize(local_path),
                content_type=detected,
            )

    def delete_file(self, file_key: str) -> None:
        self.storage.delete_object(self.bucket, self._object_name(file_key))
