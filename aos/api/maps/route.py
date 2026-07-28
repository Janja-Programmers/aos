"""
Get Route / Refresh Route.

Authenticated AOS routing endpoints backed by the internal Valhalla service.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import (
    rate_limit,
    request_ip,
)
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.services.sellers.identity import resolve_seller_reference

from .clients.valhalla_client import (
    ValhallaClientError,
    get_valhalla_client,
)
from .constants import (
    GET_ROUTE_LIMIT_PER_MINUTE_PER_IP,
    GET_ROUTE_LIMIT_PER_MINUTE_PER_USER,
    REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_IP,
    REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_USER,
    ROUTE_CACHE_TTL_SECONDS,
)
from .serializers import serialize_route_response
from .validators import (
    validate_latitude,
    validate_longitude,
    validate_route_request,
    validate_supported_location,
)


def get_route_impl(**kwargs):
    """
    Calculate a route between two or more supported locations.

    Login is required because routing is expensive.

    Supported request forms:

    1. Seller destination:
       {
         "origin_latitude": -1.286389,
         "origin_longitude": 36.817223,
         "destination_seller": "SELLER-0001"
       }

    2. locations:
       [
         {"latitude": -1.286389, "longitude": 36.817223},
         {"latitude": -1.292066, "longitude": 36.821946}
       ]

    3. origin/destination coordinates:
       {
         "origin_latitude": -1.286389,
         "origin_longitude": 36.817223,
         "destination_latitude": -1.292066,
         "destination_longitude": 36.821946
       }

    Optional:
    - costing: auto, bicycle, pedestrian
    - units: kilometers, miles
    - language: en-US
    """

    user, err = require_login()
    if err:
        return err

    rl = _route_rate_limit(
        user=user,
        operation="get_route",
        user_limit=GET_ROUTE_LIMIT_PER_MINUTE_PER_USER,
        ip_limit=GET_ROUTE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many route requests. Please try again shortly.",
    )
    if rl:
        return rl

    return _calculate_route_response(
        kwargs=kwargs,
        success_message="Route fetched successfully.",
        error_title="AOS Get Route Failed",
    )


def refresh_route_impl(**kwargs):
    """
    Refresh a route from the buyer's current location to a seller destination.

    This is stateless navigation support. Flutter handles live GPS, compass,
    off-route detection, and background tracking. Backend recalculates the
    route when Flutter requests a refresh.
    """

    user, err = require_login()
    if err:
        return err

    rl = _route_rate_limit(
        user=user,
        operation="refresh_route",
        user_limit=REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_USER,
        ip_limit=REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many route-refresh requests. Please try again shortly.",
    )
    if rl:
        return rl

    normalized_kwargs = _build_refresh_route_kwargs(
        kwargs
    )

    return _calculate_route_response(
        kwargs=normalized_kwargs,
        success_message="Route refreshed successfully.",
        error_title="AOS Refresh Route Failed",
    )


def _calculate_route_response(
    *,
    kwargs: dict,
    success_message: str,
    error_title: str,
):
    """Validate, cache, calculate, serialize, and return a route."""

    try:
        normalized_kwargs = _inject_seller_destination(
            kwargs
        )

        request = validate_route_request(
            normalized_kwargs
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
                success_message,
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
            success_message,
            data={
                "route": route,
                "cached": False,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except ValhallaClientError as ex:
        return safe_fail_from_exception(ex, fallback="Map service is temporarily unavailable.", error="MAP_SERVICE_ERROR", log_title="AOS Map Service Error")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            error_title,
        )

        return fail(
            "Failed to calculate route.",
            error="INTERNAL_ERROR",
        )


def _route_rate_limit(
    *,
    user: str,
    operation: str,
    user_limit: int,
    ip_limit: int,
    message: str,
):
    """Apply route limits by authenticated user and IP."""

    rl = rate_limit(
        key=f"aos:maps:{operation}:user:{user}",
        ttl_seconds=60,
        limit=user_limit,
        message=message,
    )
    if rl:
        return rl

    ip = request_ip()

    return rate_limit(
        key=f"aos:maps:{operation}:ip:{ip}",
        ttl_seconds=60,
        limit=ip_limit,
        message=message,
    )


def _inject_seller_destination(
    kwargs: dict,
) -> dict:
    """
    Resolve destination_seller into destination coordinates.

    Raw coordinate routing remains supported, but route-to-seller is the
    preferred AOS ecommerce path.
    """

    seller_id = _normalize_optional_string(
        kwargs.get("destination_seller")
        or kwargs.get("seller")
        or kwargs.get("seller_id")
    )

    if not seller_id:
        return kwargs

    seller_location = _get_active_seller_location(
        seller_id
    )

    updated = dict(
        kwargs
    )

    updated["destination_latitude"] = seller_location["latitude"]
    updated["destination_longitude"] = seller_location["longitude"]

    if "locations" in updated:
        updated.pop(
            "locations",
            None,
        )

    return updated


def _build_refresh_route_kwargs(
    kwargs: dict,
) -> dict:
    """Normalize refresh_route aliases to normal get_route fields."""

    current_latitude = (
        kwargs.get("current_latitude")
        if "current_latitude" in kwargs
        else kwargs.get("current_lat")
        if "current_lat" in kwargs
        else kwargs.get("latitude")
        if "latitude" in kwargs
        else kwargs.get("lat")
    )

    current_longitude = (
        kwargs.get("current_longitude")
        if "current_longitude" in kwargs
        else kwargs.get("current_lon")
        if "current_lon" in kwargs
        else kwargs.get("current_lng")
        if "current_lng" in kwargs
        else kwargs.get("longitude")
        if "longitude" in kwargs
        else kwargs.get("lon")
        if "lon" in kwargs
        else kwargs.get("lng")
    )

    if not _normalize_optional_string(
        kwargs.get("destination_seller")
        or kwargs.get("seller")
        or kwargs.get("seller_id")
    ):
        frappe.throw(
            "destination_seller is required."
        )

    updated = dict(
        kwargs
    )
    updated["origin_latitude"] = current_latitude
    updated["origin_longitude"] = current_longitude

    updated.pop(
        "locations",
        None,
    )

    return updated


def _get_active_seller_location(
    seller_id: str,
) -> dict:
    """Return active seller coordinates or raise a validation error."""

    seller_name = resolve_seller_reference(seller_id)
    seller = frappe.db.get_value(
        "AOS Seller",
        seller_name,
        [
            "name",
            "status",
            "has_location",
            "latitude",
            "longitude",
        ],
        as_dict=True,
    )

    if not seller:
        frappe.throw(
            "Seller was not found."
        )

    if seller.get("status") != "Active":
        frappe.throw(
            "Seller is not active."
        )

    if not int(seller.get("has_location") or 0):
        frappe.throw(
            "Seller has not set a map location."
        )

    latitude = validate_latitude(
        seller.get("latitude")
    )

    longitude = validate_longitude(
        seller.get("longitude")
    )

    validate_supported_location(
        latitude=latitude,
        longitude=longitude,
        label="Seller location",
    )

    return {
        "latitude": latitude,
        "longitude": longitude,
    }


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
        "aos:maps:route:v2:"
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


def _normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
