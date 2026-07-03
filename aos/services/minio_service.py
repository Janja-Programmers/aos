from __future__ import annotations

import mimetypes
import os
import uuid
from datetime import timedelta

import frappe
from minio import Minio
from minio.error import S3Error

from aos.utils.aos_config import get_minio_config
from aos.utils.aos_settings import get_aos_settings_snapshot


class MinioService:
    """
    Centralized MinIO service for AOS.

    Current responsibilities:
    - Signed downloads for processed Shorts output
    - Public URLs for processed Shorts output
    - Server-side upload helpers for MinIO-backed flows
    - Object existence checks and deletions

    Config source:
    - MinIO endpoint/secrets/buckets/public URL come from environment variables.
    - Upload expiry comes from AOS Settings as a business/media policy.
    """

    def __init__(self):
        config = get_minio_config()

        self.endpoint = config.endpoint
        self.access_key = config.access_key
        self.secret_key = config.secret_key
        self.bucket = config.bucket
        self.public_base_url = self._normalize_public_base_url(
            config.public_base_url
        )
        self.secure = bool(config.secure)
        self.base_path = (config.base_path or "shorts").strip("/")

        try:
            settings = get_aos_settings_snapshot()
            self.upload_expiry_minutes = int(
                settings.media_presigned_upload_expiry_minutes or 10
            )
        except Exception:
            self.upload_expiry_minutes = 10

        self._validate_config()

        try:
            self.client = Minio(
                endpoint=self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
            )
        except ValueError as exc:
            frappe.log_error(
                frappe.get_traceback(),
                "Invalid MinIO Endpoint Configuration",
            )
            raise Exception(
                "Invalid MINIO_ENDPOINT. Use host[:port] only, without http(s), "
                "bucket, or path. Example: minio.example.com or 127.0.0.1:9000"
            ) from exc

    @staticmethod
    def _normalize_public_base_url(value: str | None) -> str:
        """
        Normalize the public base URL used for browser/CDN-facing URLs.

        This may include http(s). It should normally not include the bucket,
        because get_public_url appends /<bucket>/<object_name>.
        """
        return (value or "").strip().rstrip("/")

    # CONFIG VALIDATION
    def _validate_config(self):
        missing = []

        if not self.endpoint:
            missing.append("MINIO_ENDPOINT")
        if not self.access_key:
            missing.append("MINIO_ACCESS_KEY or MINIO_ROOT_USER")
        if not self.secret_key:
            missing.append("MINIO_SECRET_KEY or MINIO_ROOT_PASSWORD")
        if not self.bucket:
            missing.append("AOS_MINIO_BUCKET")
        if not self.public_base_url:
            missing.append("MINIO_PUBLIC_BASE_URL or MINIO_PUBLIC_URL")

        if missing:
            raise Exception(
                "Missing MinIO environment configuration: " + ", ".join(missing)
            )

        if self.upload_expiry_minutes <= 0:
            self.upload_expiry_minutes = 10

    # FILE KEY GENERATION
    def generate_file_key(
        self,
        folder: str = "raw",
        filename: str = "video.mp4",
    ) -> str:
        folder = (folder or "raw").strip("/")

        ext = os.path.splitext(filename or "video.mp4")[1].lower()
        if not ext:
            ext = ".mp4"

        if self.base_path:
            return f"{self.base_path}/{folder}/{uuid.uuid4().hex}{ext}"

        return f"{folder}/{uuid.uuid4().hex}{ext}"

    # OBJECT NAME
    def _object_name(self, file_key: str) -> str:
        file_key = (file_key or "").strip().strip("/")
        if not file_key:
            return ""

        if self.base_path and file_key.startswith(f"{self.base_path}/"):
            return file_key.replace(f"{self.base_path}/", "", 1)

        return file_key

    # PRESIGNED UPLOAD URL
    def get_presigned_upload_url(self, file_key: str) -> str:
        try:
            return self.client.presigned_put_object(
                bucket_name=self.bucket,
                object_name=self._object_name(file_key),
                expires=timedelta(minutes=self.upload_expiry_minutes),
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "MinIO Presigned Upload Failed",
            )
            raise Exception("Failed to generate upload URL")


    # PRESIGNED DOWNLOAD URL
    def get_presigned_download_url(
        self,
        file_key: str,
        *,
        expiry_minutes: int | None = None,
    ) -> str:
        """Generate a short-lived signed GET URL for controlled downloads."""
        try:
            minutes = int(expiry_minutes or self.upload_expiry_minutes or 10)
            if minutes <= 0:
                minutes = 10

            return self.client.presigned_get_object(
                bucket_name=self.bucket,
                object_name=self._object_name(file_key),
                expires=timedelta(minutes=minutes),
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "MinIO Presigned Download Failed",
            )
            raise Exception("Failed to generate download URL")

    # PUBLIC URL
    def get_public_url(self, file_key: str) -> str:
        return f"{self.public_base_url}/{self.bucket}/{self._object_name(file_key)}"

    # VERIFY FILE EXISTS
    def file_exists(self, file_key: str) -> bool:
        try:
            self.client.stat_object(
                bucket_name=self.bucket,
                object_name=self._object_name(file_key),
            )
            return True
        except S3Error:
            return False
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "MinIO stat_object failed",
            )
            return False

    # SERVER-SIDE UPLOAD
    def upload_file(
        self,
        file_key: str,
        local_path: str,
        content_type: str | None = None,
    ):
        if not os.path.exists(local_path):
            raise Exception("Local file does not exist")

        try:
            detected_type = (
                content_type
                or mimetypes.guess_type(local_path)[0]
                or "application/octet-stream"
            )

            self.client.fput_object(
                bucket_name=self.bucket,
                object_name=self._object_name(file_key),
                file_path=local_path,
                content_type=detected_type,
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "MinIO Upload Failed",
            )
            raise Exception("File upload failed")

    # DELETE FILE
    def delete_file(self, file_key: str):
        try:
            self.client.remove_object(
                bucket_name=self.bucket,
                object_name=self._object_name(file_key),
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "MinIO Delete Failed",
            )
