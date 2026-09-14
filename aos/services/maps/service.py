"""Canonical Maps domain service."""

from __future__ import annotations

import math
from typing import Any

from aos.api.maps.clients.nominatim_client import NominatimClientError, get_nominatim_client
from aos.api.maps.clients.photon_client import PhotonClientError, get_photon_client
from aos.api.maps.clients.valhalla_client import get_valhalla_client
from aos.api.maps.constants import (
    AUTOCOMPLETE_CACHE_TTL_SECONDS,
    GEOCODER_PRIMARY_NOMINATIM,
    GEOCODER_PRIMARY_PHOTON,
    REVERSE_GEOCODE_CACHE_TTL_SECONDS,
    ROUTE_CACHE_TTL_SECONDS,
    SEARCH_CACHE_TTL_SECONDS,
)
from aos.api.maps.serializers import (
    serialize_photon_place,
    serialize_photon_place_results,
    serialize_place_search_results,
    serialize_reverse_geocode_result,
    serialize_route_response,
)
from aos.api.shared.user_display import get_user_display_map
from aos.services.sellers.constants import SELLER_MAP_POINTS_MAX_ITEMS, SELLER_MAP_POINTS_MAX_RAW_SELLERS
from aos.services.sellers.identity import require_public_seller_id
from aos.services.sellers.repository import get_route_destination, list_map_rows

from .cache import get_cached_json, maps_cache_key, set_cached_json
from .errors import MapsDependencyError, MapsNotFoundError
from .observability import maps_log
from .providers import geocoder_order, routing_enabled

_LOCATION_FIELDS = (
    "latitude",
    "longitude",
    "display_address",
    "locality",
    "region",
    "country_code",
    "location_name",
    "location_instructions",
    "location_updated_at",
)


class MapsService:
    def autocomplete(self, request: dict[str, Any]) -> dict[str, Any]:
        key = maps_cache_key("autocomplete", request)
        cached = get_cached_json(key, expected_type=list)
        if cached is not None:
            maps_log("maps.autocomplete", outcome="cache_hit", count=len(cached), cached=True)
            return {"items": cached, "count": len(cached), "cached": True}
        items, provider = self._places_with_fallback(request, autocomplete=True)
        set_cached_json(key, items, ttl_seconds=AUTOCOMPLETE_CACHE_TTL_SECONDS)
        maps_log("maps.autocomplete", provider=provider, count=len(items), cached=False)
        return {"items": items, "count": len(items), "cached": False}

    def search(self, request: dict[str, Any]) -> dict[str, Any]:
        key = maps_cache_key("search", request)
        cached = get_cached_json(key, expected_type=list)
        if cached is not None:
            maps_log("maps.search", outcome="cache_hit", count=len(cached), cached=True)
            return {"items": cached, "count": len(cached), "cached": True}
        items, provider = self._places_with_fallback(request, autocomplete=False)
        set_cached_json(key, items, ttl_seconds=SEARCH_CACHE_TTL_SECONDS)
        maps_log("maps.search", provider=provider, count=len(items), cached=False)
        return {"items": items, "count": len(items), "cached": False}

    def reverse(self, request: dict[str, Any]) -> dict[str, Any]:
        key = maps_cache_key("reverse", request)
        cached = get_cached_json(key, expected_type=dict)
        if cached is not None:
            maps_log("maps.reverse", outcome="cache_hit", cached=True)
            return {"location": cached, "cached": True}
        location = self.resolve_location(
            latitude=request["latitude"],
            longitude=request["longitude"],
            language=request.get("language") or "en",
        )
        set_cached_json(key, location, ttl_seconds=REVERSE_GEOCODE_CACHE_TTL_SECONDS)
        maps_log("maps.reverse", outcome="success", cached=False)
        return {"location": location, "cached": False}

    def resolve_location(self, *, latitude: float, longitude: float, language: str = "en") -> dict[str, Any]:
        last_error: Exception | None = None
        for provider in geocoder_order():
            try:
                if provider == GEOCODER_PRIMARY_PHOTON:
                    client = get_photon_client()
                    try:
                        features = client.reverse_geocode(
                            latitude=latitude,
                            longitude=longitude,
                            language=language,
                        )
                    finally:
                        client.close()
                    location = serialize_photon_place(features[0]) if features else None
                elif provider == GEOCODER_PRIMARY_NOMINATIM:
                    client = get_nominatim_client()
                    try:
                        raw = client.reverse_geocode(
                            latitude=latitude,
                            longitude=longitude,
                            language=language,
                        )
                    finally:
                        client.close()
                    location = serialize_reverse_geocode_result(raw)
                else:
                    continue
                if not location:
                    continue
                if not location.get("display_address"):
                    location["display_address"] = location.get("name")
                if location.get("display_address"):
                    maps_log("maps.reverse.provider", provider=provider, outcome="success")
                    return location
            except (PhotonClientError, NominatimClientError) as exc:
                last_error = exc
                maps_log("maps.provider.fallback", provider=provider, outcome="failure")
        if last_error:
            raise last_error
        raise MapsDependencyError("No usable address was found for this location.")

    def route(self, request: dict[str, Any], *, viewer: str) -> dict[str, Any]:
        locations = list(request["locations"])
        seller_reference = request.get("destination_seller")
        if seller_reference:
            seller = get_route_destination(seller_reference, viewer=viewer)
            if not seller:
                raise MapsNotFoundError("Seller location not found.")
            locations.append({"latitude": seller["latitude"], "longitude": seller["longitude"]})
        if not routing_enabled():
            raise MapsDependencyError("The routing service is disabled.")
        route_request = {
            "locations": locations,
            "costing": request["costing"],
            "units": request["units"],
            "language": request["language"],
        }
        key = maps_cache_key("route", route_request)
        cached = get_cached_json(key, expected_type=dict)
        if cached is not None:
            maps_log("maps.route", outcome="cache_hit", cached=True)
            return {"route": cached, "cached": True}
        client = get_valhalla_client()
        try:
            raw = client.get_route(**route_request)
        finally:
            client.close()
        route = serialize_route_response(raw)
        if not route.get("legs") or route.get("distance") is None or route.get("duration_seconds") is None:
            raise MapsDependencyError("The routing service returned an incomplete route.")
        set_cached_json(key, route, ttl_seconds=ROUTE_CACHE_TTL_SECONDS)
        maps_log("maps.route", provider="valhalla", cached=False)
        return {"route": route, "cached": False}

    def list_seller_map_points(self, request: dict[str, Any], *, viewer: str | None) -> dict[str, Any]:
        rows = list_map_rows(
            request,
            viewer=viewer,
            maximum_rows=SELLER_MAP_POINTS_MAX_RAW_SELLERS,
        )
        if request["zoom"] < 14 or len(rows) > SELLER_MAP_POINTS_MAX_ITEMS:
            items = _cluster_rows(rows, request["zoom"])
            response_type = "clusters"
        else:
            selected = rows[:SELLER_MAP_POINTS_MAX_ITEMS]
            displays = get_user_display_map(row.get("user") for row in selected)
            items = [_serialize_pin(row, displays) for row in selected]
            response_type = "pins"
        items = items[:SELLER_MAP_POINTS_MAX_ITEMS]
        maps_log("maps.seller_points.listed", count=len(items), status=response_type)
        return {
            "items": items,
            "count": len(items),
            "type": response_type,
            "zoom": request["zoom"],
            "truncated": len(rows) >= SELLER_MAP_POINTS_MAX_RAW_SELLERS,
            "viewport": {key: request[key] for key in ("north", "south", "east", "west")},
        }

    def _places_with_fallback(self, request: dict[str, Any], *, autocomplete: bool) -> tuple[list[dict], str]:
        last_error: Exception | None = None
        for provider in geocoder_order():
            try:
                if provider == GEOCODER_PRIMARY_PHOTON:
                    client = get_photon_client()
                    try:
                        raw = client.autocomplete_places(
                            query=request["query"],
                            limit=request["limit"],
                            latitude=request.get("latitude"),
                            longitude=request.get("longitude"),
                            country_code=request.get("country_code"),
                            language=request.get("language"),
                        )
                    finally:
                        client.close()
                    return serialize_photon_place_results(raw), "photon"
                if provider == GEOCODER_PRIMARY_NOMINATIM:
                    client = get_nominatim_client()
                    try:
                        raw = client.search_places(
                            query=request["query"],
                            limit=request["limit"],
                            country_code=request.get("country_code"),
                            language=request.get("language"),
                        )
                    finally:
                        client.close()
                    return serialize_place_search_results(raw), "nominatim"
            except (PhotonClientError, NominatimClientError) as exc:
                last_error = exc
                maps_log("maps.provider.fallback", provider=provider, outcome="failure")
        if last_error:
            raise last_error
        raise MapsDependencyError("No Maps geocoding provider is configured.")


def _cluster_rows(rows: list[dict[str, Any]], zoom: int) -> list[dict[str, Any]]:
    size = 0.5 if zoom <= 7 else 0.25 if zoom <= 9 else 0.12 if zoom <= 11 else 0.05 if zoom <= 13 else 0.02
    buckets: dict[tuple[int, int], list[float]] = {}
    for row in rows:
        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (math.floor(latitude / size), math.floor(longitude / size))
        bucket = buckets.setdefault(key, [0.0, 0.0, 0.0])
        bucket[0] += 1
        bucket[1] += latitude
        bucket[2] += longitude
    clusters = [
        {
            "type": "cluster",
            "latitude": round(values[1] / values[0], 7),
            "longitude": round(values[2] / values[0], 7),
            "count": int(values[0]),
        }
        for values in buckets.values()
        if values[0] > 0
    ]
    return sorted(clusters, key=lambda item: (-item["count"], item["latitude"], item["longitude"]))


def _serialize_pin(row: dict[str, Any], displays: dict[str, dict[str, Any]]) -> dict[str, Any]:
    display = displays.get(str(row.get("user") or ""), {})
    seller_id = require_public_seller_id(row.get("public_id"))
    return {
        "type": "seller",
        "seller_id": seller_id,
        "account_id": display.get("account_id"),
        "display_name": display.get("display_name") or "AOS User",
        "avatar": display.get("avatar"),
        "business_category": row.get("business_category"),
        "seller_type": row.get("seller_type"),
        "is_verified": bool(row.get("is_verified")),
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "location_name": row.get("location_name"),
        "locality": row.get("locality"),
        "region": row.get("region"),
        "country_code": row.get("country_code"),
    }
