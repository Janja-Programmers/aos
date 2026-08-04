from __future__ import annotations

import os
from dataclasses import dataclass


def _clean(value: object | None, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(part.strip().lower() for part in os.getenv(name, default).split(",") if part.strip())


def _float(name: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = float(default)
    if min_value is not None:
        value = max(value, min_value)
    if max_value is not None:
        value = min(value, max_value)
    return value


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


@dataclass(frozen=True)
class Settings:
    service_name: str = _clean(os.getenv("VIDEO_SERVICE_NAME"), "aos-video-processing")
    environment: str = _clean(os.getenv("VIDEO_SERVICE_ENVIRONMENT"), "development")
    debug: bool = _bool("VIDEO_SERVICE_DEBUG", False)

    redis_url: str = _clean(os.getenv("VIDEO_REDIS_URL"), "redis://video-redis:6379/0")
    queue_name: str = _clean(os.getenv("VIDEO_QUEUE_NAME"), "video")
    job_timeout_seconds: int = _int("VIDEO_JOB_TIMEOUT_SECONDS", 1800, min_value=60)
    result_ttl_seconds: int = _int("VIDEO_RESULT_TTL_SECONDS", 86400, min_value=60)
    failure_ttl_seconds: int = _int("VIDEO_FAILURE_TTL_SECONDS", 604800, min_value=60)
    durable_result_ttl_seconds: int = _int("VIDEO_DURABLE_RESULT_TTL_SECONDS", 604800, min_value=3600)
    callback_job_timeout_seconds: int = _int("VIDEO_CALLBACK_JOB_TIMEOUT_SECONDS", 120, min_value=30, max_value=900)
    callback_max_attempts: int = _int("VIDEO_CALLBACK_MAX_ATTEMPTS", 8, min_value=1, max_value=20)

    request_secret: str = _clean(os.getenv("VIDEO_SERVICE_SECRET"), "")
    callback_secret: str = _clean(os.getenv("VIDEO_SERVICE_CALLBACK_SECRET"), "")

    minio_endpoint: str = _clean(os.getenv("MINIO_ENDPOINT"), "minio:9000")
    minio_access_key: str = _clean(os.getenv("MINIO_ACCESS_KEY") or os.getenv("MINIO_ROOT_USER"), "")
    minio_secret_key: str = _clean(os.getenv("MINIO_SECRET_KEY") or os.getenv("MINIO_ROOT_PASSWORD"), "")
    minio_secure: bool = _bool("MINIO_SECURE", False)
    minio_public_base_url: str = _clean(os.getenv("MINIO_PUBLIC_BASE_URL"), "") .rstrip("/")

    output_bucket: str = _clean(os.getenv("VIDEO_OUTPUT_BUCKET"), "shorts").strip("/")
    output_base_path: str = _clean(os.getenv("VIDEO_OUTPUT_BASE_PATH"), "shorts/processed").strip("/")
    thumbnail_bucket: str = _clean(os.getenv("VIDEO_THUMBNAIL_BUCKET"), "aos-public").strip("/")
    thumbnail_base_path: str = _clean(os.getenv("VIDEO_THUMBNAIL_BASE_PATH"), "shorts/thumbnails").strip("/")

    max_duration_seconds: int = _int("VIDEO_MAX_DURATION_SECONDS", 180, min_value=1, max_value=3600)
    max_input_bytes: int = _int("VIDEO_MAX_INPUT_BYTES", 536870912, min_value=1048576, max_value=2147483648)
    max_width: int = _int("VIDEO_MAX_WIDTH", 4096, min_value=320, max_value=8192)
    max_height: int = _int("VIDEO_MAX_HEIGHT", 4096, min_value=320, max_value=8192)
    max_pixels: int = _int("VIDEO_MAX_PIXELS", 16777216, min_value=230400, max_value=67108864)
    min_aspect_ratio: float = _float("VIDEO_MIN_ASPECT_RATIO", 0.25, min_value=0.05, max_value=1.0)
    max_aspect_ratio: float = _float("VIDEO_MAX_ASPECT_RATIO", 4.0, min_value=1.0, max_value=20.0)
    ffprobe_timeout_seconds: int = _int("VIDEO_FFPROBE_TIMEOUT_SECONDS", 120, min_value=10, max_value=600)
    ffmpeg_timeout_seconds: int = _int("VIDEO_FFMPEG_TIMEOUT_SECONDS", 1800, min_value=60, max_value=7200)
    ffmpeg_threads: int = _int("VIDEO_FFMPEG_THREADS", 2, min_value=1, max_value=16)
    callback_timeout_seconds: int = _int("VIDEO_CALLBACK_TIMEOUT_SECONDS", 60, min_value=5, max_value=300)
    allowed_video_codecs: tuple[str, ...] = _csv("VIDEO_ALLOWED_CODECS", "h264,hevc,vp8,vp9,av1,mpeg4")
    callback_allowed_hosts: tuple[str, ...] = _csv("VIDEO_CALLBACK_ALLOWED_HOSTS", "")

    classification_enabled: bool = _bool("VIDEO_CLASSIFICATION_ENABLED", True)
    classification_url: str = _clean(
        os.getenv("VIDEO_CLASSIFICATION_URL"),
        "http://image-search:8000/internal/shorts/classify-frames",
    )
    classification_secret: str = _clean(os.getenv("VIDEO_CLASSIFICATION_SECRET"), "")
    classification_allowed_hosts: tuple[str, ...] = _csv(
        "VIDEO_CLASSIFICATION_ALLOWED_HOSTS", "image-search"
    )
    classification_timeout_seconds: int = _int(
        "VIDEO_CLASSIFICATION_TIMEOUT_SECONDS", 45, min_value=5, max_value=300
    )
    classification_frame_count: int = _int(
        "VIDEO_CLASSIFICATION_FRAME_COUNT", 5, min_value=1, max_value=8
    )
    classification_max_frame_bytes: int = _int(
        "VIDEO_CLASSIFICATION_MAX_FRAME_BYTES", 1048576, min_value=65536, max_value=5242880
    )


def get_settings() -> Settings:
    return Settings()
