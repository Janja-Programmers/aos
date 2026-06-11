"""
Reverse Geocode.

Public AOS reverse-geocoding endpoint backed by the internal
Nominatim service.
"""

from __future__ import annotations

import json

import frappe

from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok

from .clients.nominatim_client import (
    NominatimClientError,
    get_nominatim_client,
)
from .constants import (
    REVERSE_GEOCODE_CACHE_TTL_SECONDS,
    REVERSE_GEOCODE_LIMIT_PER_MINUTE_PER_IP,
)
from .serializers import (
    serialize_reverse_geocode_result,
)
from .validators import (
    validate_reverse_geocode_request,
)


def reverse_geocode_impl(**kwargs):
    """
    Resolve coordinates into a normalized address.

    Supported parameters:
    - latitude or lat
    - longitude or lon
    - language
    """

    ip = request_ip()

    rl = rate_limit(
        key=f"aos:maps:reverse_geocode:ip:{ip}",
        ttl_seconds=60,
        limit=(
            REVERSE_GEOCODE_LIMIT_PER_MINUTE_PER_IP
        ),
        message=(
            "Too many reverse-geocoding requests. "
            "Please try again shortly."
        ),
    )
    if rl:
        return rl

    try:
        request = validate_reverse_geocode_request(
            kwargs
        )

        language = _normalize_optional_string(
            kwargs.get("language")
        )

        cache_key = _build_reverse_cache_key(
            latitude=request["latitude"],
            longitude=request["longitude"],
            language=language,
        )

        cached = _get_cached_payload(
            cache_key
        )

        if cached is not None:
            return ok(
                "Location resolved successfully.",
                data={
                    "location": cached,
                    "cached": True,
                },
            )

        client = get_nominatim_client()

        try:
            raw_result = client.reverse_geocode(
                latitude=request["latitude"],
                longitude=request["longitude"],
                language=language,
            )
        finally:
            client.close()

        location = serialize_reverse_geocode_result(
            raw_result
        )

        _validate_serialized_location(
            location
        )

        _set_cached_payload(
            cache_key=cache_key,
            payload=location,
            ttl_seconds=(
                REVERSE_GEOCODE_CACHE_TTL_SECONDS
            ),
        )

        return ok(
            "Location resolved successfully.",
            data={
                "location": location,
                "cached": False,
            },
        )

    except frappe.ValidationError as ex:
        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except NominatimClientError as ex:
        return fail(
            str(ex),
            code="MAP_SERVICE_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Reverse Geocode Failed",
        )

        return fail(
            "Failed to resolve location.",
            code="INTERNAL_ERROR",
        )


def _validate_serialized_location(
    location: dict,
):
    """
    Ensure the normalized Nominatim response contains the minimum
    address metadata required by AOS.
    """

    if not location.get("display_address"):
        raise NominatimClientError(
            "No usable address was found for this location."
        )

    if location.get("latitude") is None:
        raise NominatimClientError(
            "The geocoding service did not return a latitude."
        )

    if location.get("longitude") is None:
        raise NominatimClientError(
            "The geocoding service did not return a longitude."
        )

    if not location.get("country_code"):
        raise NominatimClientError(
            "The geocoding service did not return a country code."
        )


def _build_reverse_cache_key(
    *,
    latitude: float,
    longitude: float,
    language: str | None,
) -> str:
    """
    Build a stable Redis key for reverse geocoding.

    Coordinates are already normalized to seven decimal places by
    the request validators.
    """

    normalized_language = (
        language.lower()
        if language
        else ""
    )

    return (
        "aos:maps:reverse:"
        f"{latitude:.7f}:"
        f"{longitude:.7f}:"
        f"{normalized_language}"
    )


def _get_cached_payload(
    cache_key: str,
) -> dict | None:
    """Read and decode a cached reverse-geocoding payload."""

    try:
        cached = frappe.cache().get_value(
            cache_key
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Maps Reverse Cache Read Failed",
        )
        return None

    if cached is None:
        return None

    if isinstance(cached, dict):
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

    if not isinstance(decoded, dict):
        return None

    return decoded


def _set_cached_payload(
    *,
    cache_key: str,
    payload: dict,
    ttl_seconds: int,
):
    """Encode and cache a reverse-geocoding payload."""

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
            "AOS Maps Reverse Cache Write Failed",
        )


def _normalize_optional_string(
    value,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(
        value
    ).strip()

    return normalized or None
