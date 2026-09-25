from __future__ import annotations

import os
from dataclasses import dataclass, field


def _clean(value: object | None, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int(name: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


def _csv(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    service_name: str = _clean(os.getenv("MODERATION_SERVICE_NAME"), "aos-content-moderation")
    environment: str = _clean(os.getenv("MODERATION_SERVICE_ENVIRONMENT"), "development")
    debug: bool = _bool("MODERATION_SERVICE_DEBUG", False)

    redis_url: str = _clean(os.getenv("MODERATION_REDIS_URL"), "redis://moderation-redis:6379/0")
    queue_name: str = _clean(os.getenv("MODERATION_QUEUE_NAME"), "moderation")
    job_timeout_seconds: int = _int("MODERATION_JOB_TIMEOUT_SECONDS", 600, min_value=60)
    result_ttl_seconds: int = _int("MODERATION_RESULT_TTL_SECONDS", 86400, min_value=60)
    failure_ttl_seconds: int = _int("MODERATION_FAILURE_TTL_SECONDS", 604800, min_value=60)
    durable_result_ttl_seconds: int = _int("MODERATION_DURABLE_RESULT_TTL_SECONDS", 604800, min_value=3600)
    callback_job_timeout_seconds: int = _int("MODERATION_CALLBACK_JOB_TIMEOUT_SECONDS", 120, min_value=30, max_value=900)
    callback_max_attempts: int = _int("MODERATION_CALLBACK_MAX_ATTEMPTS", 8, min_value=1, max_value=20)
    max_request_bytes: int = _int("MODERATION_MAX_REQUEST_BYTES", 5 * 1024 * 1024, min_value=65536, max_value=20 * 1024 * 1024)
    callback_allowed_hosts: tuple[str, ...] = _csv("MODERATION_CALLBACK_ALLOWED_HOSTS", "")

    request_secret: str = field(default=_clean(os.getenv("MODERATION_SERVICE_SECRET"), ""), repr=False)
    callback_secret: str = field(default=_clean(os.getenv("MODERATION_SERVICE_CALLBACK_SECRET"), ""), repr=False)

    minio_endpoint: str = _clean(os.getenv("MINIO_ENDPOINT"), "minio:9000")
    minio_access_key: str = field(default=_clean(os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER"), ""), repr=False)
    minio_secret_key: str = field(default=_clean(os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD"), ""), repr=False)
    minio_secure: bool = _bool("MINIO_SECURE", False)

    max_text_chars: int = _int("MODERATION_MAX_TEXT_CHARS", 20000, min_value=1000, max_value=100000)
    max_media_bytes: int = _int("MODERATION_MAX_MEDIA_BYTES", 5242880, min_value=65536, max_value=10485760)
    max_images: int = _int("MODERATION_MAX_IMAGES", 8, min_value=1, max_value=16)
    max_text_items: int = _int("MODERATION_MAX_TEXT_ITEMS", 16, min_value=1, max_value=32)
    inspect_media: bool = _bool("MODERATION_INSPECT_MEDIA", True)

    vision_url: str = _clean(os.getenv("MODERATION_VISION_URL"), "http://image-search:8000/internal/moderation/classify-images")
    vision_secret: str = field(default=_clean(os.getenv("MODERATION_VISION_SECRET"), ""), repr=False)
    vision_allowed_hosts: tuple[str, ...] = _csv("MODERATION_VISION_ALLOWED_HOSTS", "image-search")
    vision_timeout_seconds: int = _int("MODERATION_VISION_TIMEOUT_SECONDS", 45, min_value=5, max_value=180)
    vision_ready_url: str = _clean(os.getenv("MODERATION_VISION_READY_URL"), "http://image-search:8000/internal/moderation/ready")




def get_settings() -> Settings:
    return Settings()
