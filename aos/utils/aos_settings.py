"""AOS Settings helpers.

Centralizes reading the AOS Settings singleton to avoid scattered direct access.
Includes light caching because these values change rarely.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class AOSSettingsSnapshot:
    default_currency: str | None
    default_language: str | None
    default_country: str | None
    fx_provider: str | None
    fx_refresh_hours: int
    ad_expiry_days: int


def _clamp_int(val: object, default: int, min_value: int, max_value: int) -> int:
    """Best-effort int parsing with bounds.

    Settings are admin-controlled; this keeps runtime safe if a value is empty
    or misconfigured.
    """

    try:
        n = int(val)  # type: ignore[arg-type]
    except Exception:
        n = int(default)

    if n < min_value:
        return int(min_value)
    if n > max_value:
        return int(max_value)
    return int(n)


def get_aos_settings_snapshot(use_cache: bool = True) -> AOSSettingsSnapshot:
    cache = frappe.cache()
    key = "aos:settings:snapshot:v2"

    if use_cache:
        cached = cache.get_value(key)
        if isinstance(cached, dict) and "fx_refresh_hours" in cached and "ad_expiry_days" in cached:
            return AOSSettingsSnapshot(**cached)

    s = frappe.get_single("AOS Settings")

    snap = AOSSettingsSnapshot(
        default_currency=(s.default_currency or None),
        default_language=(s.default_language or None),
        default_country=(s.default_country or None),
        fx_provider=(getattr(s, "fx_provider", None) or None),
        fx_refresh_hours=_clamp_int(getattr(s, "fx_refresh_hours", 12), default=12, min_value=1, max_value=24 * 7),
        ad_expiry_days=_clamp_int(getattr(s, "ad_expiry_days", 30), default=30, min_value=1, max_value=365),
    )

    try:
        cache.set_value(key, snap.__dict__, expires_in_sec=60 * 5)
    except Exception:
        pass

    return snap
