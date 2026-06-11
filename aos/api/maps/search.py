"""
Search Places.

Public AOS map-search endpoint backed by the internal Nominatim service.
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
    SEARCH_CACHE_TTL_SECONDS,
    SEARCH_PLACES_LIMIT_PER_MINUTE_PER_IP,
)
from .serializers import serialize_place_search_results
from .validators import validate_search_request


def search_places_impl(**kwargs):
    """
    Search for places in the supported map area.

    Supported parameters:
    - query or q
    - limit
    - bounded
    - country_codes or countrycodes
    - language
    """

    ip = request_ip()

    rl = rate_limit(
        key=f"aos:maps:search_places:ip:{ip}",
        ttl_seconds=60,
        limit=SEARCH_PLACES_LIMIT_PER_MINUTE_PER_IP,
        message="Too many place-search requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        request = validate_search_request(
            kwargs
        )

        language = _normalize_optional_string(
            kwargs.get("language")
        )

        cache_key = _build_search_cache_key(
            query=request["query"],
            limit=request["limit"],
            bounded=request["bounded"],
            country_codes=request["country_codes"],
            language=language,
        )

        cached = _get_cached_payload(
            cache_key
        )

        if cached is not None:
            return ok(
                "Places fetched successfully.",
                data={
                    "items": cached,
                    "count": len(cached),
                    "cached": True,
                },
            )

        client = get_nominatim_client()

        try:
            raw_results = client.search_places(
                query=request["query"],
                limit=request["limit"],
                bounded=request["bounded"],
                country_codes=request["country_codes"],
                language=language,
            )
        finally:
            client.close()

        items = serialize_place_search_results(
            raw_results
        )

        _set_cached_payload(
            cache_key=cache_key,
            payload=items,
            ttl_seconds=SEARCH_CACHE_TTL_SECONDS,
        )

        return ok(
            "Places fetched successfully.",
            data={
                "items": items,
                "count": len(items),
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
            "AOS Search Places Failed",
        )

        return fail(
            "Failed to search places.",
            code="INTERNAL_ERROR",
        )


def _build_search_cache_key(
    *,
    query: str,
    limit: int,
    bounded: bool,
    country_codes: str,
    language: str | None,
) -> str:
    """Build a stable Redis cache key for a search request."""

    normalized_query = " ".join(
        query.lower().split()
    )

    normalized_language = (
        language.lower()
        if language
        else ""
    )

    bounded_value = (
        "1"
        if bounded
        else "0"
    )

    return (
        "aos:maps:search:"
        f"{normalized_query}:"
        f"{limit}:"
        f"{bounded_value}:"
        f"{country_codes.lower()}:"
        f"{normalized_language}"
    )


def _get_cached_payload(
    cache_key: str,
) -> list[dict] | None:
    """Read and decode a cached search payload."""

    try:
        cached = frappe.cache().get_value(
            cache_key
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Maps Search Cache Read Failed",
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
    """Encode and cache a search payload."""

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
            "AOS Maps Search Cache Write Failed",
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
