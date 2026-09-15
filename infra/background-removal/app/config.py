from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from .model_artifact import get_model_manifest, rembg_home


def _get_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _get_optional_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _get_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _get_str(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default

    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


def _get_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    raw = _get_str(name, str(default))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = default

    if minimum is not None:
        value = max(value, minimum)
    if maximum is not None:
        value = min(value, maximum)
    return value


class Settings:
    """Runtime settings actually consumed by the background-removal service."""

    def __init__(self) -> None:
        manifest = get_model_manifest()
        self.model_name = manifest.name
        self.model_sha256 = manifest.sha256
        self.rembg_home = rembg_home()
        self.service_secret = _get_optional_str("BACKGROUND_REMOVAL_SERVICE_SECRET")
        self.max_image_bytes = _get_int(
            "BACKGROUND_REMOVAL_MAX_IMAGE_BYTES",
            10 * 1024 * 1024,
            minimum=1,
        )
        self.max_image_pixels = _get_int(
            "BACKGROUND_REMOVAL_MAX_IMAGE_PIXELS",
            16_000_000,
            minimum=1,
            maximum=40_000_000,
        )
        self.max_concurrent_inferences = _get_int(
            "BACKGROUND_REMOVAL_MAX_CONCURRENT_INFERENCES",
            2,
            minimum=1,
            maximum=16,
        )
        self.inference_acquire_timeout_seconds = _get_float(
            "BACKGROUND_REMOVAL_INFERENCE_ACQUIRE_TIMEOUT_SECONDS",
            1.0,
            minimum=0.0,
            maximum=30.0,
        )

    def public_dict(self) -> dict[str, Any]:
        """Safe effective settings for health/readiness responses."""
        return {
            "model_name": self.model_name,
            "model_sha256": self.model_sha256,
            "service_auth_configured": bool(self.service_secret),
            "max_image_bytes": self.max_image_bytes,
            "max_image_pixels": self.max_image_pixels,
            "max_concurrent_inferences": self.max_concurrent_inferences,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
