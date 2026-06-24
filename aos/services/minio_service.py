from __future__ import annotations

import mimetypes
import os
import uuid
from datetime import timedelta

import frappe
from minio import Minio
from minio.error import S3Error


class MinioService:
    """
    Centralized MinIO service for AOS.

    Handles:
    - Presigned uploads
    - Public URLs
    - Server-side uploads
    - Deletions
    """
    def __init__(self):
        settings = frappe.get_single("AOS Settings")

        self.endpoint = (settings.minio_endpoint or "").strip()
        self.access_key = (settings.minio_access_key or "").strip()
        self.secret_key = settings.get_password("minio_secret_key")
        self.bucket = (settings.minio_bucket or "").strip()
        self.public_base_url = (settings.minio_public_base_url or "").rstrip("/")
        self.secure = bool(settings.minio_secure)
        self.upload_expiry_minutes = int(settings.minio_upload_expiry_minutes or 15)
        self.base_path = (settings.minio_base_path or "shorts").strip("/")

        self._validate_config()

        self.client = Minio(
            endpoint=self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

    # CONFIG VALIDATION
    def _validate_config(self):
        missing = []

        if not self.endpoint:
            missing.append("minio_endpoint")
        if not self.access_key:
            missing.append("minio_access_key")
        if not self.secret_key:
            missing.append("minio_secret_key")
        if not self.bucket:
            missing.append("minio_bucket")
        if not self.public_base_url:
            missing.append("minio_public_base_url")

        if missing:
            raise Exception(
                "Missing MinIO configuration: " + ", ".join(missing)
            )

        if self.upload_expiry_minutes <= 0:
            self.upload_expiry_minutes = 15

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

        return f"{self.base_path}/{folder}/{uuid.uuid4().hex}{ext}"

    # OBJECT NAME
    def _object_name(self, file_key: str) -> str:
        return file_key.replace(f"{self.base_path}/", "", 1)

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
            minutes = int(expiry_minutes or self.upload_expiry_minutes or 15)
            if minutes <= 0:
                minutes = 15

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
