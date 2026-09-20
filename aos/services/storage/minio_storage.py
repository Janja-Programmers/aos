"""MinIO implementation of the AOS media storage contract."""

from __future__ import annotations

import io
import json
import threading
import time
from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import BinaryIO, TypeVar
from urllib.parse import urlparse

import frappe
import urllib3
from minio import Minio
from minio.commonconfig import CopySource
from minio.datatypes import Part
from minio.error import S3Error
from urllib3.exceptions import HTTPError, MaxRetryError, ProtocolError, ReadTimeoutError

from aos.services.storage.base import (
    MultipartPart,
    ObjectStat,
    StorageConfigurationError,
    StorageError,
    StorageNotFoundError,
    StorageUnavailableError,
    StorageValidationError,
)
from aos.utils.aos_config import MinioConfig, get_minio_config

_T = TypeVar("_T")
_NOT_FOUND_CODES = {"NoSuchKey", "NoSuchBucket", "NoSuchObject", "NoSuchUpload", "NotFound"}
_RETRYABLE_CODES = {
    "InternalError",
    "RequestTimeout",
    "ServiceUnavailable",
    "SlowDown",
    "XMinioServerNotInitialized",
}

# Frappe constructs MediaService objects per API request. Reusing urllib3 pools
# preserves TCP/TLS connection reuse across those short-lived adapter instances
# without sharing credentials or MinIO client state between sites. Bucket
# existence/policy probes are likewise cached per storage endpoint for the life
# of a worker process; production media buckets are infrastructure, not
# request-scoped resources.
_HTTP_POOLS: dict[tuple[bool, int, int], urllib3.PoolManager] = {}
_HTTP_POOLS_LOCK = threading.Lock()
_ENSURED_BUCKETS: set[tuple[str, str, bool]] = set()
_ENSURED_BUCKETS_LOCK = threading.Lock()


class MinioStorage:
    """Low-level, bounded-retry MinIO adapter.

    Business concepts and authorization do not belong here. Bucket and object
    identity are accepted only from the trusted media service or signed internal
    processing services.
    """

    def __init__(self, config: MinioConfig | None = None):
        self.config = config or get_minio_config()
        self.public_base_url = self._normalize_public_base_url(self.config.public_base_url)
        self.max_retries = max(0, min(int(getattr(self.config, "max_retries", 2)), 5))
        self.retry_backoff_seconds = max(
            0.0,
            min(float(getattr(self.config, "retry_backoff_ms", 200)) / 1000.0, 5.0),
        )

        try:
            self.client = self._build_client(
                endpoint=self.config.endpoint,
                secure=bool(self.config.secure),
            )
            self.presign_client = self._build_presign_client()
        except ValueError as exc:
            raise StorageConfigurationError(
                "Invalid MINIO_ENDPOINT. Use host[:port] only, without scheme, bucket, or path."
            ) from exc

    def _build_client(self, *, endpoint: str, secure: bool) -> Minio:
        connect_timeout = max(1, min(int(getattr(self.config, "connect_timeout_seconds", 3)), 30))
        read_timeout = max(1, min(int(getattr(self.config, "read_timeout_seconds", 15)), 300))
        pool_key = (bool(secure), connect_timeout, read_timeout)
        with _HTTP_POOLS_LOCK:
            http_client = _HTTP_POOLS.get(pool_key)
            if http_client is None:
                pool_kwargs = {
                    "timeout": urllib3.Timeout(connect=connect_timeout, read=read_timeout),
                    "retries": False,
                }
                if secure:
                    pool_kwargs["cert_reqs"] = "CERT_REQUIRED"
                http_client = urllib3.PoolManager(**pool_kwargs)
                _HTTP_POOLS[pool_key] = http_client
        return Minio(
            endpoint=endpoint,
            access_key=self.config.access_key,
            secret_key=self.config.secret_key,
            secure=bool(secure),
            http_client=http_client,
        )

    def _build_presign_client(self) -> Minio:
        public_base_url = self.public_base_url
        if not public_base_url:
            return self.client

        parsed = urlparse(public_base_url)
        endpoint = (parsed.netloc or "").strip()
        path = (parsed.path or "").strip("/")
        if (
            parsed.scheme not in {"http", "https"}
            or not endpoint
            or path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise StorageConfigurationError(
                "MINIO_PUBLIC_BASE_URL must be a bare public origin such as https://files.example.com."
            )
        return self._build_client(endpoint=endpoint, secure=parsed.scheme == "https")

    @staticmethod
    def _normalize_public_base_url(value: str | None) -> str:
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            return ""
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise StorageConfigurationError("MINIO_PUBLIC_BASE_URL must use http or https.")
        if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
            raise StorageConfigurationError("MINIO_PUBLIC_BASE_URL must not include a path, query, or fragment.")
        host = (parsed.hostname or "").lower()
        if host in {"localhost", "127.0.0.1", "::1", "minio"} and _is_production():
            raise StorageConfigurationError("Production public media URL must not use an internal host.")
        return raw

    def bucket_for_type(self, bucket_type: str) -> str:
        clean = str(bucket_type or "").strip().lower()
        if clean == "public":
            return self._clean_bucket(self.config.public_bucket)
        if clean == "private":
            return self._clean_bucket(self.config.private_bucket)
        raise StorageValidationError("Invalid bucket type")

    def ensure_bucket(self, bucket: str, *, public_read: bool = False) -> None:
        clean_bucket = self._clean_bucket(bucket)
        endpoint_key = str(getattr(self.config, "endpoint", "") or "").strip().lower()
        cache_key = (endpoint_key, clean_bucket, bool(public_read))
        # A bucket already verified with public policy also satisfies a later
        # private/existence-only probe for the same endpoint/bucket.
        public_cache_key = (endpoint_key, clean_bucket, True)
        with _ENSURED_BUCKETS_LOCK:
            if cache_key in _ENSURED_BUCKETS or (
                not public_read and public_cache_key in _ENSURED_BUCKETS
            ):
                return

        def operation() -> None:
            if not self.client.bucket_exists(clean_bucket):
                try:
                    self.client.make_bucket(clean_bucket)
                except S3Error as exc:
                    if exc.code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                        raise
            if public_read:
                self._set_public_read_policy(clean_bucket)

        self._execute("ensure_bucket", operation, retryable=True)
        with _ENSURED_BUCKETS_LOCK:
            _ENSURED_BUCKETS.add(cache_key)

    def _set_public_read_policy(self, bucket: str) -> None:
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
            self.client.set_bucket_policy(bucket, json.dumps(policy, separators=(",", ":")))
        except S3Error as exc:
            # Some deployments manage policy outside the application. Access
            # denied is not hidden; public URL readiness will detect it.
            if exc.code not in {"AccessDenied", "NotImplemented"}:
                raise
            _safe_storage_log("bucket_policy_operator_managed", "non_retryable")

    def presigned_put_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        minutes = _bounded_expiry(expiry_minutes, maximum=60)
        try:
            return self.presign_client.presigned_put_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
                expires=timedelta(minutes=minutes),
            )
        except Exception as exc:
            raise StorageUnavailableError("Failed to generate upload URL") from exc


    def create_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        content_type: str,
    ) -> str:
        """Create an S3 multipart upload without proxying bytes through Frappe.

        MinIO's multipart lifecycle primitives are intentionally private in the
        pinned 7.2.x SDK. They are isolated in this adapter so the rest of AOS
        depends only on our stable StorageAdapter contract. A compatibility
        guard fails closed if a future SDK removes the primitive.
        """
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        method = getattr(self.client, "_create_multipart_upload", None)
        if not callable(method):
            raise StorageConfigurationError(
                "Installed MinIO SDK does not support multipart upload lifecycle"
            )

        def operation() -> str:
            value = method(
                clean_bucket,
                clean_key,
                {"Content-Type": str(content_type or "application/octet-stream")},
            )
            upload_id = str(value or "").strip()
            if not upload_id:
                raise StorageUnavailableError("Storage did not return a multipart upload id")
            return upload_id

        # CreateMultipartUpload is not idempotent. Retrying after an ambiguous
        # network failure can create an untracked second upload id. Let the API
        # request fail and have the caller retry initialization with its AOS
        # idempotency key instead.
        return self._execute("multipart_create", operation, retryable=False)

    def presigned_upload_part_url(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
        part_number: int,
        expiry_minutes: int,
    ) -> str:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        clean_upload_id = self._clean_upload_id(upload_id)
        safe_part = self._clean_part_number(part_number)
        minutes = _bounded_expiry(expiry_minutes, maximum=60)
        try:
            return self.presign_client.get_presigned_url(
                "PUT",
                clean_bucket,
                clean_key,
                expires=timedelta(minutes=minutes),
                extra_query_params={
                    "partNumber": str(safe_part),
                    "uploadId": clean_upload_id,
                },
            )
        except Exception as exc:
            converted = self._convert_exception(exc)
            if isinstance(converted, StorageValidationError):
                raise converted from exc
            raise StorageUnavailableError("Failed to generate multipart part URL") from exc

    def list_multipart_parts(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
    ) -> list[MultipartPart]:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        clean_upload_id = self._clean_upload_id(upload_id)
        method = getattr(self.client, "_list_parts", None)
        if not callable(method):
            raise StorageConfigurationError(
                "Installed MinIO SDK does not support multipart upload lifecycle"
            )

        def operation() -> list[MultipartPart]:
            marker = None
            parts: list[MultipartPart] = []
            while True:
                result = method(
                    clean_bucket,
                    clean_key,
                    clean_upload_id,
                    max_parts=1000,
                    part_number_marker=marker,
                )
                for item in list(getattr(result, "parts", None) or []):
                    parts.append(
                        MultipartPart(
                            part_number=int(getattr(item, "part_number", 0) or 0),
                            etag=str(getattr(item, "etag", "") or "").strip('"'),
                            size=int(getattr(item, "size", 0) or 0),
                            last_modified=getattr(item, "last_modified", None),
                        )
                    )
                if not bool(getattr(result, "is_truncated", False)):
                    break
                marker = str(getattr(result, "next_part_number_marker", "") or "").strip()
                if not marker:
                    raise StorageUnavailableError("Multipart part listing did not return a continuation marker")
            return sorted(parts, key=lambda item: item.part_number)

        return self._execute("multipart_list_parts", operation, retryable=True)

    def complete_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
        parts: list[MultipartPart],
    ) -> ObjectStat:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        clean_upload_id = self._clean_upload_id(upload_id)
        ordered = sorted(parts or [], key=lambda item: int(item.part_number))
        if not ordered:
            raise StorageValidationError("Multipart upload has no parts")
        expected = list(range(1, len(ordered) + 1))
        actual = [self._clean_part_number(item.part_number) for item in ordered]
        if actual != expected:
            raise StorageValidationError("Multipart parts must be contiguous and 1-based")
        if any(not str(item.etag or "").strip() for item in ordered):
            raise StorageValidationError("Multipart part ETag is missing")

        method = getattr(self.client, "_complete_multipart_upload", None)
        if not callable(method):
            raise StorageConfigurationError(
                "Installed MinIO SDK does not support multipart upload lifecycle"
            )

        sdk_parts = [
            Part(int(item.part_number), str(item.etag).strip('"'))
            for item in ordered
        ]

        def operation() -> None:
            method(clean_bucket, clean_key, clean_upload_id, sdk_parts)

        # CompleteMultipartUpload is mutating and an HTTP response can be lost
        # after storage has already assembled the object. Do not retry it here;
        # the Media service heals that ambiguous result on the next request by
        # checking for the assembled staging object first.
        self._execute("multipart_complete", operation, retryable=False)
        return self.stat_object(clean_bucket, clean_key)

    def abort_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
    ) -> None:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        clean_upload_id = self._clean_upload_id(upload_id)
        method = getattr(self.client, "_abort_multipart_upload", None)
        if not callable(method):
            raise StorageConfigurationError(
                "Installed MinIO SDK does not support multipart upload lifecycle"
            )

        def operation() -> None:
            method(clean_bucket, clean_key, clean_upload_id)

        try:
            self._execute("multipart_abort", operation, retryable=True)
        except StorageNotFoundError:
            return

    def presigned_get_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str:
        minutes = _bounded_expiry(expiry_minutes, maximum=60)
        try:
            return self.presign_client.presigned_get_object(
                bucket_name=self._clean_bucket(bucket),
                object_name=self._clean_object_key(object_key),
                expires=timedelta(minutes=minutes),
            )
        except Exception as exc:
            raise StorageUnavailableError("Failed to generate download URL") from exc

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)

        def operation():
            return self.client.stat_object(bucket_name=clean_bucket, object_name=clean_key)

        stat = self._execute("stat", operation, retryable=True)
        metadata = {
            str(key).lower(): str(value)
            for key, value in dict(getattr(stat, "metadata", None) or {}).items()
        }
        return ObjectStat(
            bucket=clean_bucket,
            object_key=clean_key,
            size=int(getattr(stat, "size", 0) or 0),
            etag=str(getattr(stat, "etag", "") or "") or None,
            content_type=str(getattr(stat, "content_type", "") or "") or None,
            last_modified=getattr(stat, "last_modified", None),
            metadata=metadata,
        )

    def object_exists(self, bucket: str, object_key: str) -> bool:
        try:
            self.stat_object(bucket, object_key)
            return True
        except StorageNotFoundError:
            return False

    def get_range(self, bucket: str, object_key: str, *, offset: int = 0, length: int = 65536) -> bytes:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        safe_offset = max(0, int(offset or 0))
        safe_length = max(1, min(int(length or 65536), 1024 * 1024))

        def operation() -> bytes:
            response = self.client.get_object(
                bucket_name=clean_bucket,
                object_name=clean_key,
                offset=safe_offset,
                length=safe_length,
            )
            try:
                return bytes(response.read(safe_length))
            finally:
                response.close()
                response.release_conn()

        return self._execute("get_range", operation, retryable=True)

    def iter_chunks(
        self,
        bucket: str,
        object_key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> Iterable[bytes]:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        safe_chunk = max(64 * 1024, min(int(chunk_size or 1024 * 1024), 8 * 1024 * 1024))

        try:
            response = self.client.get_object(bucket_name=clean_bucket, object_name=clean_key)
        except Exception as exc:
            self._raise_storage_error(exc, operation="get_stream")
        try:
            while True:
                chunk = response.read(safe_chunk)
                if not chunk:
                    break
                yield bytes(chunk)
        except Exception as exc:
            self._raise_storage_error(exc, operation="get_stream")
        finally:
            response.close()
            response.release_conn()

    def get_bytes(self, bucket: str, object_key: str, *, max_bytes: int | None = None) -> bytes:
        limit = max(1, int(max_bytes)) if max_bytes is not None else None
        payload = bytearray()
        for chunk in self.iter_chunks(bucket, object_key):
            payload.extend(chunk)
            if limit is not None and len(payload) > limit:
                raise StorageValidationError("Storage object exceeds allowed in-memory size")
        return bytes(payload)

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectStat:
        payload = bytes(data or b"")
        return self.put_stream(
            bucket=bucket,
            object_key=object_key,
            stream=io.BytesIO(payload),
            length=len(payload),
            content_type=content_type,
        )

    def put_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        stream: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)
        safe_length = int(length)
        if safe_length < 0:
            raise StorageValidationError("Stream length is invalid")

        def operation() -> None:
            try:
                stream.seek(0)
            except Exception:
                pass
            self.client.put_object(
                bucket_name=clean_bucket,
                object_name=clean_key,
                data=stream,
                length=safe_length,
                content_type=str(content_type or "application/octet-stream"),
            )

        self._execute("put", operation, retryable=True)
        return self.stat_object(clean_bucket, clean_key)

    def copy_object(
        self,
        *,
        source_bucket: str,
        source_object_key: str,
        destination_bucket: str,
        destination_object_key: str,
    ) -> ObjectStat:
        src_bucket = self._clean_bucket(source_bucket)
        src_key = self._clean_object_key(source_object_key)
        dst_bucket = self._clean_bucket(destination_bucket)
        dst_key = self._clean_object_key(destination_object_key)

        def operation() -> None:
            self.client.copy_object(
                bucket_name=dst_bucket,
                object_name=dst_key,
                source=CopySource(src_bucket, src_key),
            )

        self._execute("copy", operation, retryable=True)
        return self.stat_object(dst_bucket, dst_key)

    def delete_object(self, bucket: str, object_key: str) -> None:
        clean_bucket = self._clean_bucket(bucket)
        clean_key = self._clean_object_key(object_key)

        def operation() -> None:
            self.client.remove_object(bucket_name=clean_bucket, object_name=clean_key)

        try:
            self._execute("delete", operation, retryable=True)
        except StorageNotFoundError:
            return

    def build_public_url(self, bucket: str, object_key: str) -> str:
        if not self.public_base_url:
            raise StorageConfigurationError("Public media base URL is not configured")
        return f"{self.public_base_url}/{self._clean_bucket(bucket)}/{self._clean_object_key(object_key)}"

    def healthcheck(self) -> dict[str, object]:
        started = time.perf_counter()
        try:
            public_bucket = self.bucket_for_type("public")
            private_bucket = self.bucket_for_type("private")
            listed = self._execute("healthcheck", lambda: self.client.list_buckets(), retryable=True)
            existing = {str(getattr(item, "name", "") or "") for item in listed or []}
            missing_count = sum(
                1 for name in {public_bucket, private_bucket} if name not in existing
            )
            return {
                "ok": missing_count == 0 and bool(self.public_base_url),
                "configured_bucket_count": 2,
                "missing_bucket_count": missing_count,
                "public_base_url_configured": bool(self.public_base_url),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        except Exception as exc:
            return {
                "ok": False,
                "category": getattr(exc, "category", "unavailable"),
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }

    def _execute(self, operation: str, callback: Callable[[], _T], *, retryable: bool) -> _T:
        attempts = self.max_retries + 1 if retryable else 1
        for attempt in range(attempts):
            try:
                return callback()
            except Exception as exc:
                converted = self._convert_exception(exc)
                is_retryable = bool(getattr(converted, "retryable", False))
                if not is_retryable or attempt >= attempts - 1:
                    _safe_storage_log(operation, getattr(converted, "category", "failure"), attempt)
                    raise converted from exc
                time.sleep(self.retry_backoff_seconds * (2**attempt))
        raise StorageUnavailableError("Storage operation failed")

    def _raise_storage_error(self, exc: Exception, *, operation: str) -> None:
        converted = self._convert_exception(exc)
        _safe_storage_log(operation, getattr(converted, "category", "failure"))
        raise converted from exc

    @staticmethod
    def _convert_exception(exc: Exception) -> Exception:
        if isinstance(
            exc,
            (
                StorageNotFoundError,
                StorageUnavailableError,
                StorageValidationError,
                StorageConfigurationError,
            ),
        ):
            return exc
        if isinstance(exc, S3Error):
            if exc.code in _NOT_FOUND_CODES:
                return StorageNotFoundError("Storage object was not found")
            if exc.code in _RETRYABLE_CODES:
                return StorageUnavailableError("Storage is temporarily unavailable")
            if exc.code in {
                "InvalidBucketName",
                "InvalidObjectName",
                "XMinioInvalidObjectName",
                "InvalidArgument",
                "InvalidPart",
                "InvalidPartOrder",
                "EntityTooSmall",
                "EntityTooLarge",
                "MalformedXML",
            }:
                return StorageValidationError("Storage input is invalid")
            if exc.code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch"}:
                return StorageConfigurationError("Storage authorization failed")
            return StorageError("Storage operation failed")
        if isinstance(exc, (ReadTimeoutError, MaxRetryError, ProtocolError, HTTPError, TimeoutError, ConnectionError, OSError)):
            return StorageUnavailableError("Storage is temporarily unavailable")
        if isinstance(exc, ValueError):
            return StorageValidationError("Storage input is invalid")
        return StorageUnavailableError("Storage operation failed")

    @staticmethod
    def _clean_upload_id(upload_id: str) -> str:
        value = str(upload_id or "").strip()
        if not value or len(value) > 2048 or "\x00" in value:
            raise StorageValidationError("Multipart upload id is invalid")
        return value

    @staticmethod
    def _clean_part_number(part_number: int) -> int:
        try:
            value = int(part_number)
        except (TypeError, ValueError) as exc:
            raise StorageValidationError("Multipart part number is invalid") from exc
        if value < 1 or value > 10000:
            raise StorageValidationError("Multipart part number is invalid")
        return value

    @staticmethod
    def _clean_bucket(bucket: str) -> str:
        value = str(bucket or "").strip().strip("/")
        if not value or len(value) > 63:
            raise StorageValidationError("Bucket is invalid")
        if "/" in value or "\\" in value or "\x00" in value:
            raise StorageValidationError("Bucket is invalid")
        return value

    @staticmethod
    def _clean_object_key(object_key: str) -> str:
        value = str(object_key or "").strip().strip("/")
        if not value or len(value.encode("utf-8")) > 1024 or "\x00" in value:
            raise StorageValidationError("Object key is invalid")
        if "\\" in value or any(part in {"", ".", ".."} for part in value.split("/")):
            raise StorageValidationError("Object key is invalid")
        return value


def _bounded_expiry(value: int, *, maximum: int) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        minutes = 10
    return max(1, min(minutes, maximum))


def _is_production() -> bool:
    try:
        from aos.utils.aos_config import get_env

        return str(get_env("AOS_ENVIRONMENT", "development") or "development").lower() == "production"
    except Exception:
        return False


def _safe_storage_log(operation: str, category: str, retry_count: int = 0) -> None:
    try:
        frappe.logger("aos.media").warning(
            "media_storage operation=%s category=%s retry_count=%s",
            str(operation or "unknown")[:32],
            str(category or "failure")[:32],
            max(0, int(retry_count or 0)),
        )
    except Exception:
        pass
