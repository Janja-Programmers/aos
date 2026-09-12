"""Maps geocoder provider policy.

Photon is the canonical provider. Nominatim may be enabled only as an explicit
operator-controlled fallback; clients never choose providers.
"""
from __future__ import annotations

import frappe

from aos.api.maps.constants import GEOCODER_PRIMARY_NOMINATIM, GEOCODER_PRIMARY_PHOTON

MAPS_PHOTON_ENABLED_CONFIG_KEY = "maps_photon_enabled"
MAPS_NOMINATIM_FALLBACK_ENABLED_CONFIG_KEY = "maps_nominatim_fallback_enabled"


def geocoder_order() -> list[str]:
    ordered: list[str] = []
    if photon_enabled():
        ordered.append(GEOCODER_PRIMARY_PHOTON)
    if nominatim_fallback_enabled():
        ordered.append(GEOCODER_PRIMARY_NOMINATIM)
    return ordered


def photon_enabled() -> bool:
    return _boolean_config(MAPS_PHOTON_ENABLED_CONFIG_KEY, default=True)


def nominatim_fallback_enabled() -> bool:
    return _boolean_config(MAPS_NOMINATIM_FALLBACK_ENABLED_CONFIG_KEY, default=False)


def _boolean_config(key: str, *, default: bool) -> bool:
    value = frappe.conf.get(key)
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
