"""Storage contracts and public-safe storage exceptions for AOS media."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import BinaryIO, Protocol, runtime_checkable


class StorageError(RuntimeError):
    """Base class for storage failures hidden from public API clients."""

    category = "storage_error"
    retryable = False


class StorageUnavailableError(StorageError):
    category = "unavailable"
    retryable = True


class StorageNotFoundError(FileNotFoundError, StorageError):
    category = "not_found"
    retryable = False


class StorageConfigurationError(StorageError):
    category = "configuration"
    retryable = False


class StorageValidationError(ValueError, StorageError):
    category = "validation"
    retryable = False


@dataclass(frozen=True)
class ObjectStat:
    bucket: str
    object_key: str
    size: int
    etag: str | None
    content_type: str | None
    last_modified: object | None = None
    metadata: dict[str, str] | None = None


@dataclass(frozen=True)
class MultipartPart:
    """Authoritative storage metadata for one uploaded multipart part."""

    part_number: int
    etag: str
    size: int
    last_modified: object | None = None


@runtime_checkable
class StorageAdapter(Protocol):
    """Practical storage interface used by the media domain service."""

    def bucket_for_type(self, bucket_type: str) -> str: ...

    def ensure_bucket(self, bucket: str, *, public_read: bool = False) -> None: ...

    def presigned_put_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str: ...

    def create_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        content_type: str,
    ) -> str: ...

    def presigned_upload_part_url(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
        part_number: int,
        expiry_minutes: int,
    ) -> str: ...

    def list_multipart_parts(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
    ) -> list[MultipartPart]: ...

    def complete_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
        parts: list[MultipartPart],
    ) -> ObjectStat: ...

    def abort_multipart_upload(
        self,
        bucket: str,
        object_key: str,
        *,
        upload_id: str,
    ) -> None: ...

    def presigned_get_url(self, bucket: str, object_key: str, *, expiry_minutes: int) -> str: ...

    def stat_object(self, bucket: str, object_key: str) -> ObjectStat: ...

    def object_exists(self, bucket: str, object_key: str) -> bool: ...

    def get_range(self, bucket: str, object_key: str, *, offset: int = 0, length: int = 65536) -> bytes: ...

    def iter_chunks(
        self,
        bucket: str,
        object_key: str,
        *,
        chunk_size: int = 1024 * 1024,
    ) -> Iterable[bytes]: ...

    def get_bytes(self, bucket: str, object_key: str, *, max_bytes: int | None = None) -> bytes: ...

    def put_bytes(
        self,
        *,
        bucket: str,
        object_key: str,
        data: bytes,
        content_type: str,
    ) -> ObjectStat: ...

    def put_stream(
        self,
        *,
        bucket: str,
        object_key: str,
        stream: BinaryIO,
        length: int,
        content_type: str = "application/octet-stream",
    ) -> ObjectStat: ...

    def copy_object(
        self,
        *,
        source_bucket: str,
        source_object_key: str,
        destination_bucket: str,
        destination_object_key: str,
    ) -> ObjectStat: ...

    def delete_object(self, bucket: str, object_key: str) -> None: ...

    def build_public_url(self, bucket: str, object_key: str) -> str: ...

    def healthcheck(self) -> dict[str, object]: ...
