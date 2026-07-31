"""Shorts-owned object-key and public URL rules."""

from __future__ import annotations

import posixpath
from urllib.parse import urlparse

from aos.utils.aos_config import get_minio_config

from .errors import ShortsError


def validate_object_key(value: object, *, expected_prefix: str) -> str:
    key = str(value or "").strip().lstrip("/")
    normalized = posixpath.normpath(key)
    prefix = str(expected_prefix or "").strip("/")
    if not key or normalized != key or key.startswith("../") or "/../" in key:
        raise ShortsError("Invalid media object key.", code="SHORTS_INVALID_MEDIA_KEY")
    if prefix and not (key == prefix or key.startswith(prefix + "/")):
        raise ShortsError("Media object key is outside the Shorts prefix.", code="SHORTS_INVALID_MEDIA_KEY")
    return key


def public_url_for_key(*, bucket: str, object_key: str) -> str:
    config = get_minio_config()
    key = validate_object_key(object_key, expected_prefix="")
    base = str(config.public_base_url or "").rstrip("/")
    if not base:
        return ""
    parsed = urlparse(base)
    if parsed.scheme not in {"https", "http"} or not parsed.netloc:
        raise ShortsError("Invalid media public base URL.", code="SHORTS_MEDIA_CONFIGURATION_ERROR", http_status=503)
    public_bucket = str(getattr(config, "public_bucket", "") or "").strip("/")
    configured_bucket = str(bucket or "").strip("/")
    if public_bucket and configured_bucket and configured_bucket != public_bucket:
        return ""
    return f"{base}/{configured_bucket}/{key}"
