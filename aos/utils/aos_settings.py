"""AOS Settings helpers.

Centralizes reading the AOS Settings singleton to avoid scattered direct access.
Includes light caching because these values change rarely.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class AOSSettingsSnapshot:
    base_currency: str | None
    default_language: str | None
    default_country: str | None
    enable_all_countries: bool
    enable_all_languages: bool
    fx_provider: str | None
    fx_refresh_hours: int


def get_aos_settings_snapshot(use_cache: bool = True) -> AOSSettingsSnapshot:
    cache = frappe.cache()
    key = "aos:settings:snapshot:v1"

    if use_cache:
        cached = cache.get_value(key)
        if isinstance(cached, dict) and cached.get("base_currency"):
            return AOSSettingsSnapshot(**cached)

    s = frappe.get_single("AOS Settings")

    snap = AOSSettingsSnapshot(
        base_currency=(s.base_currency or None),
        default_language=(s.default_language or None),
        default_country=(s.default_country or None),
        enable_all_countries=bool(int(getattr(s, "enable_all_countries", 1) or 0)),
        enable_all_languages=bool(int(getattr(s, "enable_all_languages", 1) or 0)),
        fx_provider=(getattr(s, "fx_provider", None) or None),
        fx_refresh_hours=int(getattr(s, "fx_refresh_hours", 12) or 12),
    )

    try:
        cache.set_value(key, snap.__dict__, expires_in_sec=60 * 5)
    except Exception:
        pass

    return snap
