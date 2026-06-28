"""
Maps API validators and normalization helpers.
"""

from __future__ import annotations

import math
from typing import Any

import frappe

from .constants import (
    AUTOCOMPLETE_DEFAULT_LIMIT,
    AUTOCOMPLETE_MAX_LIMIT,
    COORDINATE_PRECISION,
    DEFAULT_SEARCH_BOUNDED,
    DEFAULT_SEARCH_COUNTRY_CODES,
    KENYA_BBOX_EAST,
    KENYA_BBOX_NORTH,
    KENYA_BBOX_SOUTH,
    KENYA_BBOX_WEST,
    LATITUDE_MAX,
    LATITUDE_MIN,
    LONGITUDE_MAX,
    LONGITUDE_MIN,
    ROUTE_ALLOWED_COSTINGS,
    ROUTE_ALLOWED_UNITS,
    ROUTE_DEFAULT_COSTING,
    ROUTE_DEFAULT_LANGUAGE,
    ROUTE_DEFAULT_UNITS,
    ROUTE_LANGUAGE_MAX_LENGTH,
    ROUTE_MAX_LOCATIONS,
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
    SEARCH_MAX_QUERY_LENGTH,
    SEARCH_MIN_QUERY_LENGTH,
    SUPPORTED_COUNTRY_CODE,
)


def validate_autocomplete_request(
    kwargs: dict,
) -> dict:
    """
    Validate and normalize an autocomplete request.

    Supported request fields:
    - query or q
    - limit
    - latitude or lat, optional location bias
    - longitude or lon/lng, optional location bias
    - country_codes or countrycodes
    """

    request = validate_search_request(
        {
            **kwargs,
            "limit": kwargs.get("limit")
            or AUTOCOMPLETE_DEFAULT_LIMIT,
        },
        default_limit=AUTOCOMPLETE_DEFAULT_LIMIT,
        max_limit=AUTOCOMPLETE_MAX_LIMIT,
    )

    latitude = validate_optional_latitude(
        kwargs.get("latitude")
        if "latitude" in kwargs
        else kwargs.get("lat")
    )

    longitude = validate_optional_longitude(
        kwargs.get("longitude")
        if "longitude" in kwargs
        else kwargs.get("lon")
        if "lon" in kwargs
        else kwargs.get("lng")
    )

    if (latitude is None) != (longitude is None):
        frappe.throw(
            "Both latitude and longitude are required when using location bias."
        )

    if latitude is not None and longitude is not None:
        validate_supported_location(
            latitude=latitude,
            longitude=longitude,
            label="Location bias",
        )

    request.update(
        {
            "latitude": latitude,
            "longitude": longitude,
        }
    )

    return request


def validate_search_request(
    kwargs: dict,
    *,
    default_limit: int = SEARCH_DEFAULT_LIMIT,
    max_limit: int = SEARCH_MAX_LIMIT,
) -> dict:
    """
    Validate and normalize a place-search request.

    Supported request fields:
    - query or q
    - limit
    - bounded
    - country_codes or countrycodes
    """

    query = normalize_required_string(
        kwargs.get("query")
        or kwargs.get("q"),
        label="Search query",
    )

    if len(query) < SEARCH_MIN_QUERY_LENGTH:
        frappe.throw(
            f"Search query must contain at least "
            f"{SEARCH_MIN_QUERY_LENGTH} characters."
        )

    if len(query) > SEARCH_MAX_QUERY_LENGTH:
        frappe.throw(
            f"Search query cannot exceed "
            f"{SEARCH_MAX_QUERY_LENGTH} characters."
        )

    limit = validate_limit(
        kwargs.get("limit"),
        default=default_limit,
        maximum=max_limit,
    )

    bounded = parse_boolean(
        kwargs.get("bounded"),
        default=DEFAULT_SEARCH_BOUNDED,
        field_label="bounded",
    )

    country_codes = normalize_country_codes(
        kwargs.get("country_codes")
        or kwargs.get("countrycodes")
        or DEFAULT_SEARCH_COUNTRY_CODES
    )

    return {
        "query": query,
        "limit": limit,
        "bounded": bounded,
        "country_codes": country_codes,
    }


def validate_reverse_geocode_request(
    kwargs: dict,
) -> dict:
    """
    Validate and normalize a reverse-geocoding request.

    Required:
    - latitude or lat
    - longitude or lon/lng
    """

    latitude = validate_latitude(
        kwargs.get("latitude")
        if "latitude" in kwargs
        else kwargs.get("lat")
    )

    longitude = validate_longitude(
        kwargs.get("longitude")
        if "longitude" in kwargs
        else kwargs.get("lon")
        if "lon" in kwargs
        else kwargs.get("lng")
    )

    validate_supported_location(
        latitude=latitude,
        longitude=longitude,
    )

    return {
        "latitude": latitude,
        "longitude": longitude,
    }


def validate_route_request(
    kwargs: dict,
) -> dict:
    """
    Validate and normalize a route request.

    Supported request forms:

    1. locations:
       [
         {"latitude": ..., "longitude": ...},
         {"latitude": ..., "longitude": ...}
       ]

    2. origin/destination:
       {
         "origin_latitude": ...,
         "origin_longitude": ...,
         "destination_latitude": ...,
         "destination_longitude": ...
       }
    """

    locations = kwargs.get("locations")

    if locations is None:
        locations = _build_origin_destination_locations(
            kwargs
        )

    normalized_locations = validate_route_locations(
        locations
    )

    costing = normalize_optional_string(
        kwargs.get("costing")
    ) or ROUTE_DEFAULT_COSTING

    costing = costing.lower()

    if costing not in ROUTE_ALLOWED_COSTINGS:
        allowed = ", ".join(
            sorted(ROUTE_ALLOWED_COSTINGS)
        )

        frappe.throw(
            f"costing must be one of: {allowed}."
        )

    units = normalize_optional_string(
        kwargs.get("units")
    ) or ROUTE_DEFAULT_UNITS

    units = units.lower()

    if units not in ROUTE_ALLOWED_UNITS:
        allowed = ", ".join(
            sorted(ROUTE_ALLOWED_UNITS)
        )

        frappe.throw(
            f"units must be one of: {allowed}."
        )

    language = normalize_optional_string(
        kwargs.get("language")
    ) or ROUTE_DEFAULT_LANGUAGE

    if len(language) > ROUTE_LANGUAGE_MAX_LENGTH:
        frappe.throw(
            f"language cannot exceed "
            f"{ROUTE_LANGUAGE_MAX_LENGTH} characters."
        )

    return {
        "locations": normalized_locations,
        "costing": costing,
        "units": units,
        "language": language,
    }


def validate_route_locations(
    locations: Any,
) -> list[dict]:
    """Validate route waypoints."""

    if not isinstance(
        locations,
        (list, tuple),
    ):
        frappe.throw(
            "locations must be a list."
        )

    if len(locations) < 2:
        frappe.throw(
            "At least two route locations are required."
        )

    if len(locations) > ROUTE_MAX_LOCATIONS:
        frappe.throw(
            f"A route cannot contain more than "
            f"{ROUTE_MAX_LOCATIONS} locations."
        )

    normalized: list[dict] = []

    for index, location in enumerate(
        locations
    ):
        if not isinstance(location, dict):
            frappe.throw(
                f"Route location {index + 1} must be an object."
            )

        latitude = validate_latitude(
            location.get("latitude")
            if "latitude" in location
            else location.get("lat")
        )

        longitude = validate_longitude(
            location.get("longitude")
            if "longitude" in location
            else location.get("lon")
            if "lon" in location
            else location.get("lng")
        )

        validate_supported_location(
            latitude=latitude,
            longitude=longitude,
            label=f"Route location {index + 1}",
        )

        normalized.append(
            {
                "latitude": latitude,
                "longitude": longitude,
            }
        )

    return normalized


def validate_optional_latitude(
    value: Any,
) -> float | None:
    """Validate optional latitude."""

    if value is None or str(value).strip() == "":
        return None

    return validate_latitude(
        value
    )


def validate_optional_longitude(
    value: Any,
) -> float | None:
    """Validate optional longitude."""

    if value is None or str(value).strip() == "":
        return None

    return validate_longitude(
        value
    )


def validate_latitude(
    value: Any,
) -> float:
    """Validate and normalize a latitude."""

    return validate_coordinate(
        value=value,
        label="Latitude",
        minimum=LATITUDE_MIN,
        maximum=LATITUDE_MAX,
    )


def validate_longitude(
    value: Any,
) -> float:
    """Validate and normalize a longitude."""

    return validate_coordinate(
        value=value,
        label="Longitude",
        minimum=LONGITUDE_MIN,
        maximum=LONGITUDE_MAX,
    )


def validate_coordinate(
    *,
    value: Any,
    label: str,
    minimum: float,
    maximum: float,
) -> float:
    """Validate and normalize one coordinate."""

    if (
        value is None
        or str(value).strip() == ""
    ):
        frappe.throw(
            f"{label} is required."
        )

    try:
        normalized = float(value)
    except (
        TypeError,
        ValueError,
    ):
        frappe.throw(
            f"{label} must be a valid number."
        )

    if not math.isfinite(normalized):
        frappe.throw(
            f"{label} must be a finite number."
        )

    if (
        normalized < minimum
        or normalized > maximum
    ):
        frappe.throw(
            f"{label} must be between "
            f"{minimum:g} and {maximum:g}."
        )

    return round(
        normalized,
        COORDINATE_PRECISION,
    )


def validate_supported_location(
    *,
    latitude: float,
    longitude: float,
    label: str = "Location",
):
    """
    Ensure a coordinate lies inside the currently supported map extract.

    Version 1 supports the Kenya extract.
    """

    if not is_supported_location(
        latitude=latitude,
        longitude=longitude,
    ):
        frappe.throw(
            f"{label} is outside the supported AOS Maps coverage area."
        )


def is_supported_location(
    *,
    latitude: float,
    longitude: float,
) -> bool:
    """Return whether a coordinate is inside the supported map extract."""

    return (
        KENYA_BBOX_SOUTH
        <= latitude
        <= KENYA_BBOX_NORTH
        and KENYA_BBOX_WEST
        <= longitude
        <= KENYA_BBOX_EAST
    )


def supported_area_bounds() -> dict:
    """Return the supported map bounds as a normalized viewport object."""

    return {
        "north": KENYA_BBOX_NORTH,
        "south": KENYA_BBOX_SOUTH,
        "east": KENYA_BBOX_EAST,
        "west": KENYA_BBOX_WEST,
    }


def viewport_intersects_supported_area(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
) -> bool:
    """Return whether a viewport intersects the supported map area."""

    if north < KENYA_BBOX_SOUTH or south > KENYA_BBOX_NORTH:
        return False

    if east < KENYA_BBOX_WEST or west > KENYA_BBOX_EAST:
        return False

    return True


def validate_supported_viewport(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
    label: str = "Viewport",
):
    """Ensure a viewport intersects the currently supported map extract."""

    if not viewport_intersects_supported_area(
        north=north,
        south=south,
        east=east,
        west=west,
    ):
        frappe.throw(
            f"{label} is outside the supported AOS Maps coverage area."
        )


def clamp_bbox_to_supported_area(
    *,
    north: float,
    south: float,
    east: float,
    west: float,
) -> dict | None:
    """Clamp a viewport/bounding box to the supported map area.

    Returns None when the supplied box does not intersect the supported area.
    """

    if not viewport_intersects_supported_area(
        north=north,
        south=south,
        east=east,
        west=west,
    ):
        return None

    return {
        "north": min(north, KENYA_BBOX_NORTH),
        "south": max(south, KENYA_BBOX_SOUTH),
        "east": min(east, KENYA_BBOX_EAST),
        "west": max(west, KENYA_BBOX_WEST),
    }


def validate_limit(
    value: Any,
    *,
    default: int,
    maximum: int,
) -> int:
    """Validate a safe positive integer limit."""

    if value is None or value == "":
        return default

    try:
        normalized = int(value)
    except (
        TypeError,
        ValueError,
    ):
        frappe.throw(
            "limit must be a valid integer."
        )

    if normalized <= 0:
        frappe.throw(
            "limit must be greater than zero."
        )

    return min(
        normalized,
        maximum,
    )


def normalize_required_string(
    value: Any,
    *,
    label: str,
) -> str:
    """Normalize and validate a required string."""

    normalized = normalize_optional_string(
        value
    )

    if normalized is None:
        frappe.throw(
            f"{label} is required."
        )

    return normalized


def normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim optional text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(
        value
    ).strip()

    return normalized or None


def parse_boolean(
    value: Any,
    *,
    default: bool,
    field_label: str,
) -> bool:
    """Parse a boolean request value."""

    if value is None or value == "":
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value in {0, 1}:
            return bool(value)
        frappe.throw(
            f"{field_label} must be true or false."
        )

    normalized = str(
        value
    ).strip().lower()

    if normalized in {
        "1",
        "true",
        "yes",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
    }:
        return False

    frappe.throw(
        f"{field_label} must be true or false."
    )


def normalize_country_codes(
    value: Any,
) -> str:
    """
    Normalize and restrict country codes.

    V1 intentionally supports Kenya only.
    """

    normalized = normalize_optional_string(
        value
    )

    if not normalized:
        return DEFAULT_SEARCH_COUNTRY_CODES

    country_codes = [
        code.strip().lower()
        for code in normalized.split(",")
        if code.strip()
    ]

    if not country_codes:
        return DEFAULT_SEARCH_COUNTRY_CODES

    supported_lower = SUPPORTED_COUNTRY_CODE.lower()

    for code in country_codes:
        if (
            len(code) != 2
            or not code.isalpha()
        ):
            frappe.throw(
                "country_codes must contain valid 2-letter country codes."
            )

        if code != supported_lower:
            frappe.throw(
                f"Only {SUPPORTED_COUNTRY_CODE} is currently supported."
            )

    return ",".join(
        country_codes
    )


def _build_origin_destination_locations(
    kwargs: dict,
) -> list[dict]:
    """Build route locations from origin/destination fields."""

    return [
        {
            "latitude": kwargs.get("origin_latitude")
            if "origin_latitude" in kwargs
            else kwargs.get("origin_lat"),
            "longitude": kwargs.get("origin_longitude")
            if "origin_longitude" in kwargs
            else kwargs.get("origin_lon")
            if "origin_lon" in kwargs
            else kwargs.get("origin_lng"),
        },
        {
            "latitude": kwargs.get("destination_latitude")
            if "destination_latitude" in kwargs
            else kwargs.get("destination_lat"),
            "longitude": kwargs.get("destination_longitude")
            if "destination_longitude" in kwargs
            else kwargs.get("destination_lon")
            if "destination_lon" in kwargs
            else kwargs.get("destination_lng"),
        },
    ]
