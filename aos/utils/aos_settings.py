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
    )

    try:
        cache.set_value(key, snap.__dict__, expires_in_sec=60 * 5)
    except Exception:
        pass

    return snap
