"""Canonical Maps domain service."""

from __future__ import annotations

import math
from typing import Any

from frappe.utils import now_datetime

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
from aos.api.shared.user_display import get_user_display, get_user_display_map
from aos.api.sellers.constants import SELLER_MAP_POINTS_MAX_ITEMS, SELLER_MAP_POINTS_MAX_RAW_SELLERS
from aos.api.sellers.serializers import serialize_seller_location
from aos.services.sellers.errors import SellerStateError
from aos.services.sellers.identity import (
    migration_fallback_public_seller_id,
    normalize_public_seller_id,
    public_seller_id_for_name,
)

from .cache import get_cached_json, maps_cache_key, set_cached_json
from .errors import MapsConflictError, MapsDependencyError, MapsNotFoundError
from .observability import maps_log
from .providers import geocoder_order, routing_enabled
from .repository import (
    get_public_seller_location,
    get_seller_location_for_user,
    list_seller_map_rows,
    lock_seller_for_location_mutation,
)

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
            seller = get_public_seller_location(seller_reference, viewer=viewer)
            if not seller or not bool(seller.get("has_location")):
                raise MapsNotFoundError("Seller location not found.")
            locations.append(
                {
                    "latitude": float(seller["latitude"]),
                    "longitude": float(seller["longitude"]),
                }
            )
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

    def get_seller_location(self, *, seller_reference: str | None, viewer: str | None) -> dict[str, Any]:
        is_owner = not seller_reference
        if is_owner:
            seller = get_seller_location_for_user(str(viewer or ""))
        else:
            seller = get_public_seller_location(str(seller_reference), viewer=viewer)
        if not seller:
            raise MapsNotFoundError("Seller location not found.")
        display = get_user_display(seller.get("user"))
        seller_id = normalize_public_seller_id(seller.get("public_id")) or public_seller_id_for_name(seller.get("name"))
        maps_log("maps.seller_location.read", outcome="success", status="owner" if is_owner else "public")
        return {
            "seller": seller_id,
            "seller_id": seller_id,
            "user": display.get("account_id"),
            "is_owner": bool(is_owner or seller.get("user") == viewer),
            "location_version": int(seller.get("location_version") or 0),
            "location": serialize_seller_location(seller),
        }

    def prepare_location_update(
        self,
        *,
        user: str,
        request: dict[str, Any],
    ) -> dict[str, Any] | None:
        seller = get_seller_location_for_user(user)
        if not seller:
            raise MapsNotFoundError("Seller profile not found.")
        if str(seller.get("status") or "").strip() != "Active":
            raise SellerStateError(
                "Seller account is not active.",
                code="SELLER_INACTIVE",
                http_status=403,
                data={"status": str(seller.get("status") or "").strip() or None},
            )
        if _location_input_identical(seller, request):
            return None
        current_version = int(seller.get("location_version") or 0)
        expected = request.get("expected_version")
        if expected is not None and expected != current_version:
            raise MapsConflictError(
                "Seller location has changed. Refresh and try again.",
                data={"current_version": current_version},
            )
        return self.resolve_location(
            latitude=request["latitude"],
            longitude=request["longitude"],
            language="en",
        )

    def set_seller_location(
        self,
        *,
        user: str,
        request: dict[str, Any],
        resolved_location: dict[str, Any] | None,
    ) -> dict[str, Any]:
        seller = lock_seller_for_location_mutation(user)
        if not seller:
            raise MapsNotFoundError("Seller profile not found.")
        current_version = int(seller.get("location_version") or 0)
        input_identical = _location_input_identical(seller, request)
        expected = request.get("expected_version")
        if input_identical:
            return self._location_mutation_payload(seller, changed=False)
        if expected is not None and expected != current_version:
            raise MapsConflictError(
                "Seller location has changed. Refresh and try again.",
                data={"current_version": current_version},
            )
        if resolved_location is None:
            raise MapsConflictError(
                "Seller location changed while the request was being prepared. Refresh and try again.",
                data={"current_version": current_version},
            )
        desired = {
            "latitude": request["latitude"],
            "longitude": request["longitude"],
            "location_name": request.get("location_name"),
            "location_instructions": request.get("location_instructions"),
            "display_address": resolved_location["display_address"],
            "locality": resolved_location.get("locality"),
            "region": resolved_location.get("region"),
            "country_code": resolved_location["country_code"],
        }
        for field, value in desired.items():
            seller.set(field, value)
        seller.has_location = 1
        seller.location_updated_at = now_datetime()
        seller.location_version = current_version + 1
        seller.flags.aos_seller_location_action = True
        seller.save(ignore_permissions=True)
        maps_log("maps.seller_location.updated", outcome="success")
        return self._location_mutation_payload(seller, changed=True)

    def remove_seller_location(self, *, user: str, request: dict[str, Any]) -> dict[str, Any]:
        seller = lock_seller_for_location_mutation(user)
        if not seller:
            raise MapsNotFoundError("Seller profile not found.")
        current_version = int(seller.get("location_version") or 0)
        expected = request.get("expected_version")
        has_location = bool(seller.get("has_location"))
        if expected is not None and expected != current_version and has_location:
            raise MapsConflictError(
                "Seller location has changed. Refresh and try again.",
                data={"current_version": current_version},
            )
        if not has_location:
            return self._location_mutation_payload(seller, changed=False)
        for field in _LOCATION_FIELDS:
            seller.set(field, None)
        seller.has_location = 0
        seller.location_version = current_version + 1
        seller.flags.aos_seller_location_action = True
        seller.save(ignore_permissions=True)
        maps_log("maps.seller_location.removed", outcome="success")
        return self._location_mutation_payload(seller, changed=True)

    def list_seller_map_points(self, request: dict[str, Any], *, viewer: str | None) -> dict[str, Any]:
        rows = list_seller_map_rows(
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

    @staticmethod
    def _location_mutation_payload(seller, *, changed: bool) -> dict[str, Any]:
        seller_id = public_seller_id_for_name(seller.name)
        return {
            "seller": seller_id,
            "seller_id": seller_id,
            "location_version": int(seller.get("location_version") or 0),
            "changed": bool(changed),
            "location": serialize_seller_location(seller),
        }



def _location_input_identical(seller: Any, request: dict[str, Any]) -> bool:
    return bool(seller.get("has_location")) and all(
        _same_value(seller.get(field), request.get(field))
        for field in ("latitude", "longitude", "location_name", "location_instructions")
    )

def _same_value(left: Any, right: Any) -> bool:
    if isinstance(right, float):
        try:
            return math.isclose(float(left), right, rel_tol=0, abs_tol=0.0000001)
        except (TypeError, ValueError):
            return False
    return (str(left).strip() if left is not None else None) == (
        str(right).strip() if right is not None else None
    )


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
    seller_id = normalize_public_seller_id(row.get("public_id")) or migration_fallback_public_seller_id(row.get("name"))
    return {
        "type": "seller",
        "seller": seller_id,
        "seller_id": seller_id,
        "user": display.get("account_id"),
        "display_name": display.get("display_name") or row.get("full_name"),
        "avatar": display.get("avatar") or row.get("user_image"),
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
