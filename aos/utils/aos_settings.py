"""AOS Settings helpers.

Centralizes reading the AOS Settings singleton to avoid scattered direct access.
Includes light caching because these values change rarely.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class AOSSettingsSnapshot:
    # Localization
    default_currency: str | None
    default_language: str | None
    default_country: str | None

    # Foreign Exchange
    base_currency: str | None
    refresh_hours: int

    # Ads
    ad_expiry_days: int
    flash_sale_window_days: int

    # Image Search (Qdrant)
    qdrant_host: str | None
    qdrant_port: int
    qdrant_collection: str | None
    qdrant_api_key: str | None
    qdrant_https: int

    image_search_limit: int
    score_threshold: float

    # Storage (MinIO)
    minio_endpoint: str | None
    minio_access_key: str | None
    minio_bucket: str
    minio_public_base_url: str
    minio_secure: int
    minio_upload_expiry_minutes: int
    minio_base_path: str

    # Connect (LiveKit)
    livekit_endpoint: str | None
    livekit_token_ttl_minutes: int


def _clamp_int(val: object, default: int, min_value: int, max_value: int) -> int:
    """Best-effort int parsing with bounds safety."""
    try:
        n = int(val)  # type: ignore[arg-type]
    except Exception:
        n = int(default)

    if n < min_value:
        return int(min_value)
    if n > max_value:
        return int(max_value)
    return int(n)


def _clamp_float(val: object, default: float, min_value: float, max_value: float) -> float:
    """Best-effort float parsing with bounds safety."""
    try:
        n = float(val)  # type: ignore[arg-type]
    except Exception:
        n = float(default)

    if n < min_value:
        return float(min_value)
    if n > max_value:
        return float(max_value)
    return float(n)


def get_aos_settings_snapshot(use_cache: bool = True) -> AOSSettingsSnapshot:
    cache = frappe.cache()
    key = "aos:settings:snapshot:v2"

    if use_cache:
        cached = cache.get_value(key)
        if isinstance(cached, dict) and "refresh_hours" in cached:
            return AOSSettingsSnapshot(**cached)

    s = frappe.get_single("AOS Settings")

    snap = AOSSettingsSnapshot(
        # Localization
        default_currency=(s.default_currency or None),
        default_language=(s.default_language or None),
        default_country=(s.default_country or None),

        # Foreign Exchange
        base_currency=(s.base_currency or None),
        refresh_hours=_clamp_int(
            getattr(s, "refresh_hours", 12),
            default=12,
            min_value=1,
            max_value=24 * 7,
        ),

        # Ads
        ad_expiry_days=_clamp_int(
            getattr(s, "ad_expiry_days", 30),
            default=30,
            min_value=1,
            max_value=365,
        ),

        flash_sale_window_days=_clamp_int(
            getattr(s, "flash_sale_window_days", 7),
            default=7,
            min_value=1,
            max_value=60,
        ),

        # Image Search (Qdrant)
        qdrant_host=(getattr(s, "qdrant_host", None) or "localhost"),
        qdrant_port=_clamp_int(
            getattr(s, "qdrant_port", 6333),
            default=6333,
            min_value=1,
            max_value=65535,
        ),
        qdrant_collection=(getattr(s, "qdrant_collection", None) or "ads"),
        qdrant_api_key=(getattr(s, "qdrant_api_key", None) or None),
        qdrant_https=_clamp_int(
            getattr(s, "qdrant_https", 0),
            default=0,
            min_value=0,
            max_value=1,
        ),

        image_search_limit=_clamp_int(
            getattr(s, "image_search_limit", 50),
            default=50,
            min_value=1,
            max_value=200,
        ),

        score_threshold=_clamp_float(
            getattr(s, "score_threshold", 0.0),
            default=0.0,
            min_value=0.0,
            max_value=1.0,
        ),

        # Storage (MinIO)
        minio_endpoint=(getattr(s, "minio_endpoint", None) or "localhost:9100"),

        minio_access_key=(getattr(s, "minio_access_key", None) or "minio"),

        minio_bucket=(getattr(s, "minio_bucket", None) or "shorts"),

        minio_public_base_url=(
            getattr(s, "minio_public_base_url", None)
            or "http://localhost:9100/shorts"
        ),

        minio_secure=_clamp_int(
            getattr(s, "minio_secure", 0),
            default=0,
            min_value=0,
            max_value=1,
        ),

        minio_upload_expiry_minutes=_clamp_int(
            getattr(s, "minio_upload_expiry_minutes", 10),
            default=10,
            min_value=1,
            max_value=60,
        ),

        minio_base_path=(getattr(s, "minio_base_path", None) or "shorts"),

        # Connect (LiveKit)
        livekit_endpoint=(getattr(s, "livekit_endpoint", None) or None),

        livekit_token_ttl_minutes=_clamp_int(
            getattr(s, "livekit_token_ttl_minutes", 60),
            default=60,
            min_value=1,
            max_value=1440,
        ),

    )

    try:
        cache.set_value(key, snap.__dict__, expires_in_sec=60 * 5)
    except Exception:
        pass

    return snap
