"""Generic MinIO storage helper for AOS media.

This is intentionally low-level. It knows buckets, object keys, and MinIO; it
should not know about Ads, Chat, Reviews, or other business concepts.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import BinaryIO

import frappe
from minio import Minio
from minio.error import S3Error

from aos.utils.aos_config import MinioConfig, get_minio_config


@dataclass(frozen=True)
class ObjectStat:
    bucket: str
    object_key: str
    size: int
    etag: str | None
    content_type: str | None
    last_modified: object | None = None


class MinioStorage:
    """Low-level MinIO client wrapper."""

    def __init__(self, config: MinioConfig | None = None):
        self.config = config or get_minio_config()
        self.public_base_url = (self.config.public_base_url or "").strip().rstrip("/")

        try:
            self.client = Minio(
                endpoint=self.config.endpoint,
                access_key=self.config.access_key,
                secret_key=self.config.secret_key,
                secure=bool(self.config.secure),
            )
        except ValueError as exc:
            raise RuntimeError(
                "Invalid MINIO_ENDPOINT. Use host[:port] only, without http(s), bucket, or path."
            ) from exc

    def bucket_for_type(self, bucket_type: str) -> str:
        bucket_type = str(bucket_type or "").strip().lower()
        if bucket_type == "public":
            return self.config.public_bucket
        if bucket_type == "private":
            return self.config.private_bucket
        raise ValueError("Invalid bucket type")

    def ensure_bucket(self, bucket: str, *, public_read: bool = False) -> None:
        bucket = self._clean_bucket(bucket)
        try:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

            if public_read:
                self._set_public_read_policy(bucket)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Ensure Bucket Failed")
            raise RuntimeError("Failed to prepare storage bucket")

    def _set_public_read_policy(self, bucket: str) -> None:
        """Best-effort public read policy for public media bucket."""
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ],
        }
        try:
            self.client.set_bucket_policy(bucket, json.dumps(policy))
        except Exception:
            # Bucket creation should not fail just because policy update is
            # unavailable. Public URLs can still be served by signed URLs or an
            # operator-managed bucket policy.
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Public Policy Failed")

    def presigned_put_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        try:
            return self.client.presigned_put_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
                expires=timedelta(minutes=max(1, int(expiry_minutes or 10))),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Presigned PUT Failed")
            raise RuntimeError("Failed to generate upload URL")

    def presigned_get_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        try:
            return self.client.presigned_get_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
                expires=timedelta(minutes=max(1, int(expiry_minutes or 10))),
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Presigned GET Failed")
            raise RuntimeError("Failed to generate download URL")

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat:
        try:
            stat = self.client.stat_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
            )
            return ObjectStat(
                bucket=self._clean_bucket(bucket),
                object_key=self._clean_object_key(object_key),
                size=int(getattr(stat, "size", 0) or 0),
                etag=getattr(stat, "etag", None),
                content_type=getattr(stat, "content_type", None),
                last_modified=getattr(stat, "last_modified", None),
            )
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                raise FileNotFoundError("Object not found") from exc
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Stat Failed")
            raise RuntimeError("Failed to inspect storage object") from exc
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Stat Failed")
            raise RuntimeError("Failed to inspect storage object") from exc

    def object_exists(self, bucket: str, object_key: str) -> bool:
        try:
            self.stat_object(bucket, object_key)
            return True
        except FileNotFoundError:
            return False

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        response = None
        try:
            response = self.client.get_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
            )
            return response.read()
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Get Bytes Failed")
            raise RuntimeError("Failed to read storage object")
        finally:
            if response is not None:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat:
        payload = bytes(data or b"")
        try:
            self.client.put_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
                data=io.BytesIO(payload),
                length=len(payload),
                content_type=content_type or "application/octet-stream",
            )
            return self.stat_object(bucket, object_key)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Put Bytes Failed")
            raise RuntimeError("Failed to write storage object")

    def put_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        stream: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat:
        try:
            self.client.put_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
                data=stream,
                length=int(length),
                content_type=content_type or "application/octet-stream",
            )
            return self.stat_object(bucket, object_key)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Put Stream Failed")
            raise RuntimeError("Failed to write storage object")

    def delete_object(self, bucket: str, object_key: str) -> None:
        try:
            self.client.remove_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
            )
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}:
                return
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Delete Failed")
            raise RuntimeError("Failed to delete storage object") from exc
        except Exception as exc:
            frappe.log_error(frappe.get_traceback(), "AOS MinIO Delete Failed")
            raise RuntimeError("Failed to delete storage object") from exc

    def build_public_url(self, bucket: str, object_key: str) -> str:
        if not self.public_base_url:
            return ""
        return f"{self.public_base_url}/{self._clean_bucket(bucket)}/{self._clean_object_key(object_key)}"

    @staticmethod
    def _clean_bucket(bucket: str) -> str:
        value = str(bucket or "").strip().strip("/")
        if not value:
            raise ValueError("Bucket is required")
        return value

    @staticmethod
    def _clean_object_key(object_key: str) -> str:
        value = str(object_key or "").strip().strip("/")
        if not value:
            raise ValueError("Object key is required")
        if "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
            raise ValueError("Invalid object key")
        return value
