"""
Maps API validators and normalization helpers.
"""

from __future__ import annotations

import math
from typing import Any

import frappe

from .constants import (
    COORDINATE_PRECISION,
    DEFAULT_SEARCH_BOUNDED,
    DEFAULT_SEARCH_COUNTRY_CODES,
    LATITUDE_MAX,
    LATITUDE_MIN,
    LONGITUDE_MAX,
    LONGITUDE_MIN,
    MOMBASA_BBOX_EAST,
    MOMBASA_BBOX_NORTH,
    MOMBASA_BBOX_SOUTH,
    MOMBASA_BBOX_WEST,
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


def validate_search_request(
    kwargs: dict,
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
        default=SEARCH_DEFAULT_LIMIT,
        maximum=SEARCH_MAX_LIMIT,
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
    - longitude or lon
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

    Version 1 supports the Mombasa regional extract only.
    """

    if not (
        MOMBASA_BBOX_SOUTH
        <= latitude
        <= MOMBASA_BBOX_NORTH
        and MOMBASA_BBOX_WEST
        <= longitude
        <= MOMBASA_BBOX_EAST
    ):
        frappe.throw(
            f"{label} is outside the currently supported map area."
        )


def validate_limit(
    value: Any,
    *,
    default: int,
    maximum: int,
) -> int:
    """Validate a positive bounded result limit."""

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


def parse_boolean(
    value: Any,
    *,
    default: bool | None = None,
    field_label: str = "value",
) -> bool:
    """Parse common boolean request values."""

    if value is None or value == "":
        if default is not None:
            return default

        frappe.throw(
            f"{field_label} is required."
        )

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value == 1:
            return True

        if value == 0:
            return False

    normalized = str(
        value
    ).strip().lower()

    if normalized in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if normalized in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    frappe.throw(
        f"{field_label} must be true or false."
    )


def normalize_country_codes(
    value: Any,
) -> str:
    """
    Normalize comma-separated ISO alpha-2 country codes.

    Version 1 only supports Kenya.
    """

    if isinstance(value, (list, tuple, set)):
        raw_codes = value
    else:
        raw_codes = str(
            value or ""
        ).split(",")

    normalized_codes = []

    for raw_code in raw_codes:
        code = normalize_optional_string(
            raw_code
        )

        if not code:
            continue

        code = code.upper()

        if (
            len(code) != 2
            or not code.isalpha()
        ):
            frappe.throw(
                "country_codes must contain valid "
                "2-letter country codes."
            )

        if code != SUPPORTED_COUNTRY_CODE:
            frappe.throw(
                "Only Kenya is currently supported."
            )

        if code not in normalized_codes:
            normalized_codes.append(code)

    if not normalized_codes:
        normalized_codes = [
            SUPPORTED_COUNTRY_CODE,
        ]

    return ",".join(
        code.lower()
        for code in normalized_codes
    )


def normalize_required_string(
    value: Any,
    *,
    label: str,
) -> str:
    """Normalize required text."""

    normalized = normalize_optional_string(
        value
    )

    if not normalized:
        frappe.throw(
            f"{label} is required."
        )

    return normalized


def normalize_optional_string(
    value: Any,
) -> str | None:
    """Trim text and normalize empty values to None."""

    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None


def _build_origin_destination_locations(
    kwargs: dict,
) -> list[dict]:
    """Build route locations from origin and destination parameters."""

    required_fields = (
        "origin_latitude",
        "origin_longitude",
        "destination_latitude",
        "destination_longitude",
    )

    missing = [
        fieldname
        for fieldname in required_fields
        if kwargs.get(fieldname) is None
        or str(kwargs.get(fieldname)).strip() == ""
    ]

    if missing:
        frappe.throw(
            "locations or complete origin and destination "
            "coordinates are required."
        )

    return [
        {
            "latitude": kwargs.get(
                "origin_latitude"
            ),
            "longitude": kwargs.get(
                "origin_longitude"
            ),
        },
        {
            "latitude": kwargs.get(
                "destination_latitude"
            ),
            "longitude": kwargs.get(
                "destination_longitude"
            ),
        },
    ]
