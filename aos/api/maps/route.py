"""
Get Route.

Public AOS routing endpoint backed by the internal Valhalla service.
"""

from __future__ import annotations

import json

import frappe

from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok

from .clients.valhalla_client import (
    ValhallaClientError,
    get_valhalla_client,
)
from .constants import (
    GET_ROUTE_LIMIT_PER_MINUTE_PER_IP,
    ROUTE_CACHE_TTL_SECONDS,
)
from .serializers import serialize_route_response
from .validators import validate_route_request


def get_route_impl(**kwargs):
    """
    Calculate a route between two or more supported locations.

    Supported request forms:

    1. locations:
       [
         {
           "latitude": -4.0435,
           "longitude": 39.6682
         },
         {
           "latitude": -4.0610719,
           "longitude": 39.6532746
         }
       ]

    2. origin/destination:
       {
         "origin_latitude": -4.0435,
         "origin_longitude": 39.6682,
         "destination_latitude": -4.0610719,
         "destination_longitude": 39.6532746
       }

    Optional:
    - costing: auto, bicycle, pedestrian
    - units: kilometers, miles
    - language: en-US
    """

    ip = request_ip()

    rl = rate_limit(
        key=f"aos:maps:get_route:ip:{ip}",
        ttl_seconds=60,
        limit=GET_ROUTE_LIMIT_PER_MINUTE_PER_IP,
        message=(
            "Too many route requests. "
            "Please try again shortly."
        ),
    )
    if rl:
        return rl

    try:
        request = validate_route_request(
            kwargs
        )

        cache_key = _build_route_cache_key(
            locations=request["locations"],
            costing=request["costing"],
            units=request["units"],
            language=request["language"],
        )

        cached = _get_cached_payload(
            cache_key
        )

        if cached is not None:
            return ok(
                "Route fetched successfully.",
                data={
                    "route": cached,
                    "cached": True,
                },
            )

        client = get_valhalla_client()

        try:
            raw_route = client.get_route(
                locations=request["locations"],
                costing=request["costing"],
                units=request["units"],
                language=request["language"],
            )
        finally:
            client.close()

        route = serialize_route_response(
            raw_route
        )

        _validate_serialized_route(
            route
        )

        _set_cached_payload(
            cache_key=cache_key,
            payload=route,
            ttl_seconds=ROUTE_CACHE_TTL_SECONDS,
        )

        return ok(
            "Route fetched successfully.",
            data={
                "route": route,
                "cached": False,
            },
        )

    except frappe.ValidationError as ex:
        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except ValhallaClientError as ex:
        return fail(
            str(ex),
            code="MAP_SERVICE_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Route Failed",
        )

        return fail(
            "Failed to calculate route.",
            code="INTERNAL_ERROR",
        )


def _validate_serialized_route(
    route: dict,
):
    """
    Ensure the normalized Valhalla response contains the minimum route
    data required by Flutter.
    """

    legs = route.get(
        "legs"
    )

    if not isinstance(
        legs,
        list,
    ) or not legs:
        raise ValhallaClientError(
            "No route could be found."
        )

    if route.get("distance") is None:
        raise ValhallaClientError(
            "The routing service did not return route distance."
        )

    if route.get("duration_seconds") is None:
        raise ValhallaClientError(
            "The routing service did not return route duration."
        )

    for index, leg in enumerate(
        legs
    ):
        if not isinstance(
            leg,
            dict,
        ):
            raise ValhallaClientError(
                f"Route leg {index + 1} is invalid."
            )

        if not leg.get("shape"):
            raise ValhallaClientError(
                f"Route leg {index + 1} has no geometry."
            )

        maneuvers = leg.get(
            "maneuvers"
        )

        if not isinstance(
            maneuvers,
            list,
        ):
            raise ValhallaClientError(
                f"Route leg {index + 1} has invalid maneuvers."
            )


def _build_route_cache_key(
    *,
    locations: list[dict],
    costing: str,
    units: str,
    language: str,
) -> str:
    """
    Build a stable Redis cache key for a route request.

    Coordinates are normalized to seven decimal places by validators.
    """

    location_parts = [
        (
            f"{location['latitude']:.7f},"
            f"{location['longitude']:.7f}"
        )
        for location in locations
    ]

    normalized_language = (
        language.strip().lower()
    )

    return (
        "aos:maps:route:"
        f"{'|'.join(location_parts)}:"
        f"{costing.lower()}:"
        f"{units.lower()}:"
        f"{normalized_language}"
    )


def _get_cached_payload(
    cache_key: str,
) -> dict | None:
    """Read and decode a cached route payload."""

    try:
        cached = frappe.cache().get_value(
            cache_key
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Maps Route Cache Read Failed",
        )

        return None

    if cached is None:
        return None

    if isinstance(
        cached,
        dict,
    ):
        return cached

    if isinstance(
        cached,
        bytes,
    ):
        try:
            cached = cached.decode(
                "utf-8"
            )
        except UnicodeDecodeError:
            return None

    if not isinstance(
        cached,
        str,
    ):
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

    if not isinstance(
        decoded,
        dict,
    ):
        return None

    return decoded


def _set_cached_payload(
    *,
    cache_key: str,
    payload: dict,
    ttl_seconds: int,
):
    """Encode and cache a route payload."""

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
            "AOS Maps Route Cache Write Failed",
        )
