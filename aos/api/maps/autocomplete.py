"""
Autocomplete Places.

Public AOS autocomplete endpoint backed primarily by the internal Photon
service with Nominatim fallback.
"""

from __future__ import annotations

import json

import frappe

from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception

from .clients.nominatim_client import (
    NominatimClientError,
    get_nominatim_client,
)
from .clients.photon_client import (
    PhotonClientError,
    get_photon_client,
)
from .constants import (
    DEFAULT_GEOCODER_FALLBACK,
    DEFAULT_GEOCODER_PRIMARY,
    GEOCODER_PRIMARY_NOMINATIM,
    GEOCODER_PRIMARY_PHOTON,
    MAPS_GEOCODER_FALLBACK_CONFIG_KEY,
    MAPS_GEOCODER_PRIMARY_CONFIG_KEY,
    AUTOCOMPLETE_CACHE_TTL_SECONDS,
    AUTOCOMPLETE_PLACES_LIMIT_PER_MINUTE_PER_IP,
)
from .serializers import (
    serialize_photon_place_results,
    serialize_place_search_results,
)
from .validators import validate_autocomplete_request


def autocomplete_places_impl(**kwargs):
    """
    Fast place autocomplete within the supported map area.

    Supported parameters:
    - query or q
    - limit
    - latitude / longitude, optional location bias
    - country_codes or countrycodes
    - language
    """

    ip = request_ip()

    rl = rate_limit(
        key=f"aos:maps:autocomplete_places:ip:{ip}",
        ttl_seconds=60,
        limit=AUTOCOMPLETE_PLACES_LIMIT_PER_MINUTE_PER_IP,
        message="Too many autocomplete requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        request = validate_autocomplete_request(
            kwargs
        )

        language = _normalize_optional_string(
            kwargs.get("language")
        )

        cache_key = _build_autocomplete_cache_key(
            query=request["query"],
            limit=request["limit"],
            country_codes=request["country_codes"],
            latitude=request["latitude"],
            longitude=request["longitude"],
            language=language,
        )

        cached = _get_cached_payload(
            cache_key
        )

        if cached is not None:
            return ok(
                "Place suggestions fetched successfully.",
                data={
                    "items": cached,
                    "count": len(cached),
                    "cached": True,
                    "source": "cache",
                },
            )

        items, source = _autocomplete_with_fallback(
            request=request,
            language=language,
        )

        _set_cached_payload(
            cache_key=cache_key,
            payload=items,
            ttl_seconds=AUTOCOMPLETE_CACHE_TTL_SECONDS,
        )

        return ok(
            "Place suggestions fetched successfully.",
            data={
                "items": items,
                "count": len(items),
                "cached": False,
                "source": source,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

    except (
        PhotonClientError,
        NominatimClientError,
    ) as ex:
        return safe_fail_from_exception(ex, fallback="Map service is temporarily unavailable.", code="MAP_SERVICE_ERROR", log_title="AOS Map Service Error")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Autocomplete Places Failed",
        )

        return fail(
            "Failed to fetch place suggestions.",
            code="INTERNAL_ERROR",
        )


def _autocomplete_with_fallback(
    *,
    request: dict,
    language: str | None,
) -> tuple[list[dict], str]:
    """Autocomplete places using configured primary/fallback geocoders."""

    last_error: Exception | None = None

    for provider in _geocoder_order():
        if provider == GEOCODER_PRIMARY_PHOTON:
            try:
                client = get_photon_client()

                try:
                    raw_results = client.autocomplete_places(
                        query=request["query"],
                        limit=request["limit"],
                        latitude=request["latitude"],
                        longitude=request["longitude"],
                        country_codes=request["country_codes"],
                        language=language,
                    )
                finally:
                    client.close()

                return serialize_photon_place_results(
                    raw_results
                ), "photon"

            except PhotonClientError as ex:
                last_error = ex
                frappe.log_error(
                    frappe.get_traceback(),
                    "AOS Photon Autocomplete Fallback",
                )

        elif provider == GEOCODER_PRIMARY_NOMINATIM:
            try:
                client = get_nominatim_client()

                try:
                    raw_results = client.search_places(
                        query=request["query"],
                        limit=request["limit"],
                        bounded=True,
                        country_codes=request["country_codes"],
                        language=language,
                    )
                finally:
                    client.close()

                return serialize_place_search_results(
                    raw_results
                ), "nominatim"

            except NominatimClientError as ex:
                last_error = ex
                frappe.log_error(
                    frappe.get_traceback(),
                    "AOS Nominatim Autocomplete Fallback",
                )

    if last_error is not None:
        raise last_error

    raise NominatimClientError(
        "No geocoding provider is configured."
    )


def _build_autocomplete_cache_key(
    *,
    query: str,
    limit: int,
    country_codes: str,
    latitude: float | None,
    longitude: float | None,
    language: str | None,
) -> str:
    """Build a stable Redis cache key for an autocomplete request."""

    normalized_query = " ".join(
        query.lower().split()
    )

    normalized_language = (
        language.lower()
        if language
        else ""
    )

    bias = ""

    if latitude is not None and longitude is not None:
        # Round bias to reduce cache fragmentation while preserving locality.
        bias = f"{latitude:.3f},{longitude:.3f}"

    return (
        "aos:maps:autocomplete:v1:"
        f"{normalized_query}:"
        f"{limit}:"
        f"{country_codes.lower()}:"
        f"{bias}:"
        f"{normalized_language}"
    )


def _get_cached_payload(
    cache_key: str,
) -> list[dict] | None:
    """Read and decode a cached autocomplete payload."""

    try:
        cached = frappe.cache().get_value(
            cache_key
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Maps Autocomplete Cache Read Failed",
        )
        return None

    if cached is None:
        return None

    if isinstance(cached, list):
        return cached

    if isinstance(cached, bytes):
        try:
            cached = cached.decode(
                "utf-8"
            )
        except UnicodeDecodeError:
            return None

    if not isinstance(cached, str):
        return None

    try:
        decoded = json.loads(
            cached
        )
    except (
        TypeError,
        ValueError,
    ):
        return None

    if not isinstance(decoded, list):
        return None

    return [
        item
        for item in decoded
        if isinstance(item, dict)
    ]


def _set_cached_payload(
    *,
    cache_key: str,
    payload: list[dict],
    ttl_seconds: int,
):
    """Encode and cache an autocomplete payload."""

    try:
        frappe.cache().set_value(
            cache_key,
            json.dumps(
                payload,
                separators=(",", ":"),
                ensure_ascii=False,
            ),
            expires_in_sec=ttl_seconds,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Maps Autocomplete Cache Write Failed",
        )


def _geocoder_order() -> list[str]:
    """Return primary/fallback geocoder order from site config."""

    primary = _normalize_optional_string(
        frappe.conf.get(MAPS_GEOCODER_PRIMARY_CONFIG_KEY)
    ) or DEFAULT_GEOCODER_PRIMARY

    fallback = _normalize_optional_string(
        frappe.conf.get(MAPS_GEOCODER_FALLBACK_CONFIG_KEY)
    ) or DEFAULT_GEOCODER_FALLBACK

    order = []

    for item in [primary, fallback]:
        item = item.lower()
        if item in {
            GEOCODER_PRIMARY_PHOTON,
            GEOCODER_PRIMARY_NOMINATIM,
        } and item not in order:
            order.append(item)

    if not order:
        order = [
            GEOCODER_PRIMARY_PHOTON,
            GEOCODER_PRIMARY_NOMINATIM,
        ]

    return order


def _normalize_optional_string(
    value,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
