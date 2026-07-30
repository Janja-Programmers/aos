"""Configured Maps provider selection and bounded fallback policy."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.maps.constants import (
    DEFAULT_GEOCODER_FALLBACK,
    DEFAULT_GEOCODER_PRIMARY,
    GEOCODER_PRIMARY_ALLOWED,
    GEOCODER_PRIMARY_NOMINATIM,
    GEOCODER_PRIMARY_PHOTON,
    MAPS_GEOCODER_FALLBACK_CONFIG_KEY,
    MAPS_GEOCODER_PRIMARY_CONFIG_KEY,
)

MAPS_PHOTON_ENABLED_CONFIG_KEY = "maps_photon_enabled"


def geocoder_order() -> list[str]:
    primary = _provider_value(
        frappe.conf.get(MAPS_GEOCODER_PRIMARY_CONFIG_KEY),
        default=DEFAULT_GEOCODER_PRIMARY,
    )
    fallback = _provider_value(
        frappe.conf.get(MAPS_GEOCODER_FALLBACK_CONFIG_KEY),
        default=DEFAULT_GEOCODER_FALLBACK,
        allow_blank=True,
    )
    ordered: list[str] = []
    for provider in (primary, fallback):
        if not provider or provider in ordered:
            continue
        if provider == GEOCODER_PRIMARY_PHOTON and not photon_enabled():
            continue
        ordered.append(provider)
    if GEOCODER_PRIMARY_NOMINATIM not in ordered:
        ordered.append(GEOCODER_PRIMARY_NOMINATIM)
    return ordered


def photon_enabled() -> bool:
    value = frappe.conf.get(MAPS_PHOTON_ENABLED_CONFIG_KEY)
    if value is None or value == "":
        # The maintained Compose stack intentionally omits Photon. It becomes
        # active only after operators explicitly enable the verified image.
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _provider_value(value: Any, *, default: str, allow_blank: bool = False) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "" if allow_blank and value is not None else default
    return normalized if normalized in GEOCODER_PRIMARY_ALLOWED else default
