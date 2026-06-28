"""
Maps API serializers.

Normalizes raw responses from:
- Photon
- Nominatim
- Valhalla

The Flutter client should consume these stable AOS payloads instead of
depending directly on third-party service response formats.
"""

from __future__ import annotations

from typing import Any

from .constants import (
    COUNTRY_CODE_LENGTH,
    COUNTRY_NAME_MAX_LENGTH,
    DISPLAY_ADDRESS_MAX_LENGTH,
    LOCALITY_MAX_LENGTH,
    PLACE_NAME_MAX_LENGTH,
    POSTCODE_MAX_LENGTH,
    REGION_MAX_LENGTH,
    ROUTE_SHAPE_FORMAT,
)


def serialize_place_search_results(
    results: list[dict],
) -> list[dict]:
    """Serialize raw Nominatim search results."""

    serialized: list[dict] = []

    for result in results:
        place = serialize_place(result)

        if place is not None:
            serialized.append(place)

    return serialized


def serialize_photon_place_results(
    results: list[dict],
) -> list[dict]:
    """Serialize raw Photon feature results."""

    serialized: list[dict] = []

    for result in results:
        place = serialize_photon_place(
            result
        )

        if place is not None:
            serialized.append(place)

    return serialized


def serialize_place(
    raw: dict,
) -> dict | None:
    """Serialize one Nominatim place result."""

    latitude = _to_optional_float(
        raw.get("lat")
    )
    longitude = _to_optional_float(
        raw.get("lon")
    )

    if latitude is None or longitude is None:
        return None

    address = _as_dict(
        raw.get("address")
    )

    bounding_box = _serialize_bounding_box(
        raw.get("boundingbox")
    )

    name = _extract_place_name(
        raw=raw,
        address=address,
    )

    display_address = _normalize_optional_string(
        raw.get("display_name"),
        max_length=DISPLAY_ADDRESS_MAX_LENGTH,
    )

    locality = _extract_locality(
        address
    )

    region = _extract_region(
        address
    )

    country = _normalize_optional_string(
        address.get("country"),
        max_length=COUNTRY_NAME_MAX_LENGTH,
    )

    country_code = _normalize_country_code(
        address.get("country_code")
    )

    postcode = _normalize_optional_string(
        address.get("postcode"),
        max_length=POSTCODE_MAX_LENGTH,
    )

    return {
        "place_id": _to_optional_int(
            raw.get("place_id")
        ),
        "osm_type": _normalize_optional_string(
            raw.get("osm_type"),
            max_length=20,
        ),
        "osm_id": _to_optional_int(
            raw.get("osm_id")
        ),
        "name": name,
        "display_address": display_address,
        "latitude": latitude,
        "longitude": longitude,
        "category": _normalize_optional_string(
            raw.get("category")
            or raw.get("class"),
            max_length=100,
        ),
        "type": _normalize_optional_string(
            raw.get("type"),
            max_length=100,
        ),
        "address_type": _normalize_optional_string(
            raw.get("addresstype"),
            max_length=100,
        ),
        "locality": locality,
        "region": region,
        "postcode": postcode,
        "country": country,
        "country_code": country_code,
        "bounding_box": bounding_box,
        "importance": _to_optional_float(
            raw.get("importance")
        ),
        "source": "nominatim",
    }


def serialize_photon_place(
    raw: dict,
) -> dict | None:
    """Serialize one Photon GeoJSON feature."""

    geometry = _as_dict(
        raw.get("geometry")
    )

    coordinates = geometry.get(
        "coordinates"
    )

    if not isinstance(
        coordinates,
        (list, tuple),
    ) or len(coordinates) < 2:
        return None

    longitude = _to_optional_float(
        coordinates[0]
    )
    latitude = _to_optional_float(
        coordinates[1]
    )

    if latitude is None or longitude is None:
        return None

    properties = _as_dict(
        raw.get("properties")
    )

    name = _normalize_optional_string(
        properties.get("name"),
        max_length=PLACE_NAME_MAX_LENGTH,
    )

    locality = _first_normalized_string(
        properties.get("district"),
        properties.get("city"),
        properties.get("town"),
        properties.get("village"),
        properties.get("locality"),
        max_length=LOCALITY_MAX_LENGTH,
    )

    region = _first_normalized_string(
        properties.get("state"),
        properties.get("county"),
        max_length=REGION_MAX_LENGTH,
    )

    country = _normalize_optional_string(
        properties.get("country"),
        max_length=COUNTRY_NAME_MAX_LENGTH,
    )

    country_code = _normalize_country_code(
        properties.get("countrycode")
        or properties.get("country_code")
    )

    display_address = _build_photon_display_address(
        name=name,
        properties=properties,
        locality=locality,
        region=region,
        country=country,
    )

    return {
        "place_id": _to_optional_int(
            properties.get("osm_id")
        ),
        "osm_type": _normalize_optional_string(
            properties.get("osm_type"),
            max_length=20,
        ),
        "osm_id": _to_optional_int(
            properties.get("osm_id")
        ),
        "name": name,
        "display_address": display_address,
        "latitude": latitude,
        "longitude": longitude,
        "category": _normalize_optional_string(
            properties.get("osm_key"),
            max_length=100,
        ),
        "type": _normalize_optional_string(
            properties.get("osm_value"),
            max_length=100,
        ),
        "address_type": _normalize_optional_string(
            properties.get("type")
            or properties.get("osm_value"),
            max_length=100,
        ),
        "locality": locality,
        "region": region,
        "postcode": _normalize_optional_string(
            properties.get("postcode"),
            max_length=POSTCODE_MAX_LENGTH,
        ),
        "country": country,
        "country_code": country_code,
        "bounding_box": _serialize_photon_extent(
            properties.get("extent")
        ),
        "importance": _to_optional_float(
            properties.get("importance")
        ),
        "source": "photon",
    }


def serialize_reverse_geocode_result(
    raw: dict,
) -> dict:
    """Serialize one raw Nominatim reverse-geocoding response."""

    latitude = _to_optional_float(
        raw.get("lat")
    )
    longitude = _to_optional_float(
        raw.get("lon")
    )

    address = _as_dict(
        raw.get("address")
    )

    return {
        "place_id": _to_optional_int(
            raw.get("place_id")
        ),
        "osm_type": _normalize_optional_string(
            raw.get("osm_type"),
            max_length=20,
        ),
        "osm_id": _to_optional_int(
            raw.get("osm_id")
        ),
        "name": _extract_place_name(
            raw=raw,
            address=address,
        ),
        "display_address": _normalize_optional_string(
            raw.get("display_name"),
            max_length=DISPLAY_ADDRESS_MAX_LENGTH,
        ),
        "latitude": latitude,
        "longitude": longitude,
        "category": _normalize_optional_string(
            raw.get("category")
            or raw.get("class"),
            max_length=100,
        ),
        "type": _normalize_optional_string(
            raw.get("type"),
            max_length=100,
        ),
        "address_type": _normalize_optional_string(
            raw.get("addresstype"),
            max_length=100,
        ),
        "road": _normalize_optional_string(
            address.get("road"),
            max_length=PLACE_NAME_MAX_LENGTH,
        ),
        "house_number": _normalize_optional_string(
            address.get("house_number"),
            max_length=100,
        ),
        "neighbourhood": _normalize_optional_string(
            address.get("neighbourhood"),
            max_length=LOCALITY_MAX_LENGTH,
        ),
        "quarter": _normalize_optional_string(
            address.get("quarter"),
            max_length=LOCALITY_MAX_LENGTH,
        ),
        "suburb": _normalize_optional_string(
            address.get("suburb"),
            max_length=LOCALITY_MAX_LENGTH,
        ),
        "locality": _extract_locality(
            address
        ),
        "region": _extract_region(
            address
        ),
        "postcode": _normalize_optional_string(
            address.get("postcode"),
            max_length=POSTCODE_MAX_LENGTH,
        ),
        "country": _normalize_optional_string(
            address.get("country"),
            max_length=COUNTRY_NAME_MAX_LENGTH,
        ),
        "country_code": _normalize_country_code(
            address.get("country_code")
        ),
        "bounding_box": _serialize_bounding_box(
            raw.get("boundingbox")
        ),
        "source": "nominatim",
    }


def serialize_route_response(
    raw: dict,
) -> dict:
    """Serialize a raw Valhalla route response."""

    trip = _as_dict(
        raw.get("trip")
    )

    summary = _as_dict(
        trip.get("summary")
    )

    raw_legs = trip.get(
        "legs"
    )

    if not isinstance(raw_legs, list):
        raw_legs = []

    legs = [
        serialize_route_leg(
            leg=leg,
            leg_index=index,
        )
        for index, leg in enumerate(
            raw_legs
        )
        if isinstance(leg, dict)
    ]

    locations = _serialize_route_locations(
        trip.get("locations")
    )

    total_distance = _first_available_float(
        summary.get("length"),
        summary.get("distance"),
    )

    total_duration = _first_available_float(
        summary.get("time"),
        summary.get("duration"),
    )

    return {
        "status": _to_optional_int(
            trip.get("status")
        ),
        "status_message": _normalize_optional_string(
            trip.get("status_message"),
            max_length=500,
        ),
        "units": _normalize_optional_string(
            trip.get("units"),
            max_length=30,
        ),
        "language": _normalize_optional_string(
            trip.get("language"),
            max_length=30,
        ),
        "shape_format": ROUTE_SHAPE_FORMAT,
        "distance": total_distance,
        "distance_display": _format_distance(
            total_distance,
            trip.get("units"),
        ),
        "duration_seconds": total_duration,
        "duration_display": _format_duration(
            total_duration
        ),
        "traffic_enabled": False,
        "traffic_source": None,
        "locations": locations,
        "legs": legs,
    }


def serialize_route_leg(
    *,
    leg: dict,
    leg_index: int,
) -> dict:
    """Serialize one Valhalla route leg."""

    summary = _as_dict(
        leg.get("summary")
    )

    raw_maneuvers = leg.get(
        "maneuvers"
    )

    if not isinstance(raw_maneuvers, list):
        raw_maneuvers = []

    maneuvers = [
        serialize_route_maneuver(
            maneuver=maneuver,
            maneuver_index=index,
        )
        for index, maneuver in enumerate(
            raw_maneuvers
        )
        if isinstance(maneuver, dict)
    ]

    distance = _first_available_float(
        summary.get("length"),
        summary.get("distance"),
    )

    duration = _first_available_float(
        summary.get("time"),
        summary.get("duration"),
    )

    units = summary.get("units")

    return {
        "index": leg_index,
        "distance": distance,
        "distance_display": _format_distance(
            distance,
            units,
        ),
        "duration_seconds": duration,
        "duration_display": _format_duration(
            duration
        ),
        "shape": _normalize_optional_string(
            leg.get("shape"),
        ),
        "shape_format": ROUTE_SHAPE_FORMAT,
        "maneuvers": maneuvers,
    }


def serialize_route_maneuver(
    *,
    maneuver: dict,
    maneuver_index: int,
) -> dict:
    """Serialize one Valhalla maneuver."""

    instruction = _normalize_optional_string(
        maneuver.get("instruction"),
        max_length=1000,
    )

    verbal_transition_alert = _normalize_optional_string(
        maneuver.get(
            "verbal_transition_alert_instruction"
        ),
        max_length=1000,
    )

    verbal_pre_transition = _normalize_optional_string(
        maneuver.get(
            "verbal_pre_transition_instruction"
        ),
        max_length=1000,
    )

    verbal_post_transition = _normalize_optional_string(
        maneuver.get(
            "verbal_post_transition_instruction"
        ),
        max_length=1000,
    )

    street_names = _normalize_string_list(
        maneuver.get("street_names"),
        max_items=10,
        max_length=PLACE_NAME_MAX_LENGTH,
    )

    begin_street_names = _normalize_string_list(
        maneuver.get("begin_street_names"),
        max_items=10,
        max_length=PLACE_NAME_MAX_LENGTH,
    )

    sign = _serialize_maneuver_sign(
        maneuver.get("sign")
    )

    distance = _first_available_float(
        maneuver.get("length"),
        maneuver.get("distance"),
    )

    duration = _first_available_float(
        maneuver.get("time"),
        maneuver.get("duration"),
    )

    return {
        "index": maneuver_index,
        "type": _to_optional_int(
            maneuver.get("type")
        ),
        "instruction": instruction,
        "verbal_transition_alert_instruction": (
            verbal_transition_alert
        ),
        "verbal_pre_transition_instruction": (
            verbal_pre_transition
        ),
        "verbal_post_transition_instruction": (
            verbal_post_transition
        ),
        "street_names": street_names,
        "begin_street_names": begin_street_names,
        "distance": distance,
        "distance_display": _format_distance(
            distance,
            None,
        ),
        "duration_seconds": duration,
        "duration_display": _format_duration(
            duration
        ),
        "begin_shape_index": _to_optional_int(
            maneuver.get("begin_shape_index")
        ),
        "end_shape_index": _to_optional_int(
            maneuver.get("end_shape_index")
        ),
        "travel_mode": _normalize_optional_string(
            maneuver.get("travel_mode"),
            max_length=50,
        ),
        "travel_type": _normalize_optional_string(
            maneuver.get("travel_type"),
            max_length=50,
        ),
        "toll": bool(
            maneuver.get("toll")
        ),
        "rough": bool(
            maneuver.get("rough")
        ),
        "highway": bool(
            maneuver.get("highway")
        ),
        "ferry": bool(
            maneuver.get("ferry")
        ),
        "roundabout_exit_count": _to_optional_int(
            maneuver.get("roundabout_exit_count")
        ),
        "sign": sign,
    }


def _serialize_route_locations(
    value: Any,
) -> list[dict]:
    """Serialize Valhalla route locations."""

    if not isinstance(value, list):
        return []

    serialized = []

    for index, location in enumerate(value):
        if not isinstance(location, dict):
            continue

        latitude = _first_available_float(
            location.get("lat"),
            location.get("latitude"),
        )

        longitude = _first_available_float(
            location.get("lon"),
            location.get("longitude"),
        )

        serialized.append(
            {
                "index": index,
                "latitude": latitude,
                "longitude": longitude,
                "type": _normalize_optional_string(
                    location.get("type"),
                    max_length=30,
                ),
                "original_index": _to_optional_int(
                    location.get("original_index")
                ),
                "side_of_street": _normalize_optional_string(
                    location.get("side_of_street"),
                    max_length=30,
                ),
            }
        )

    return serialized


def _serialize_maneuver_sign(
    value: Any,
) -> dict | None:
    """Serialize Valhalla sign information."""

    if not isinstance(value, dict):
        return None

    payload = {
        "exit_numbers": _serialize_sign_items(
            value.get("exit_number_elements")
        ),
        "exit_branches": _serialize_sign_items(
            value.get("exit_branch_elements")
        ),
        "exit_toward": _serialize_sign_items(
            value.get("exit_toward_elements")
        ),
        "exit_names": _serialize_sign_items(
            value.get("exit_name_elements")
        ),
    }

    if not any(payload.values()):
        return None

    return payload


def _serialize_sign_items(
    value: Any,
) -> list[str]:
    """Serialize Valhalla sign-element arrays."""

    if not isinstance(value, list):
        return []

    result: list[str] = []

    for item in value:
        if isinstance(item, dict):
            text = _normalize_optional_string(
                item.get("text"),
                max_length=PLACE_NAME_MAX_LENGTH,
            )
        else:
            text = _normalize_optional_string(
                item,
                max_length=PLACE_NAME_MAX_LENGTH,
            )

        if text and text not in result:
            result.append(text)

    return result


def _extract_place_name(
    *,
    raw: dict,
    address: dict,
) -> str | None:
    """Extract the most useful place name from Nominatim data."""

    candidates = [
        raw.get("name"),
        address.get("amenity"),
        address.get("shop"),
        address.get("tourism"),
        address.get("leisure"),
        address.get("office"),
        address.get("building"),
        address.get("historic"),
        address.get("attraction"),
        address.get("road"),
        address.get("neighbourhood"),
        address.get("quarter"),
        address.get("suburb"),
        address.get("city"),
        address.get("town"),
        address.get("village"),
    ]

    for candidate in candidates:
        normalized = _normalize_optional_string(
            candidate,
            max_length=PLACE_NAME_MAX_LENGTH,
        )

        if normalized:
            return normalized

    display_name = _normalize_optional_string(
        raw.get("display_name"),
        max_length=DISPLAY_ADDRESS_MAX_LENGTH,
    )

    if not display_name:
        return None

    return _normalize_optional_string(
        display_name.split(",", 1)[0],
        max_length=PLACE_NAME_MAX_LENGTH,
    )


def _extract_locality(
    address: dict,
) -> str | None:
    """Extract the most useful local-area value."""

    candidates = [
        address.get("neighbourhood"),
        address.get("quarter"),
        address.get("suburb"),
        address.get("city_district"),
        address.get("hamlet"),
        address.get("village"),
        address.get("town"),
        address.get("city"),
        address.get("municipality"),
    ]

    for candidate in candidates:
        normalized = _normalize_optional_string(
            candidate,
            max_length=LOCALITY_MAX_LENGTH,
        )

        if normalized:
            return normalized

    return None


def _extract_region(
    address: dict,
) -> str | None:
    """Extract a region/state/county value."""

    candidates = [
        address.get("state"),
        address.get("region"),
        address.get("state_district"),
        address.get("county"),
    ]

    for candidate in candidates:
        normalized = _normalize_optional_string(
            candidate,
            max_length=REGION_MAX_LENGTH,
        )

        if normalized:
            return normalized

    return None


def _build_photon_display_address(
    *,
    name: str | None,
    properties: dict,
    locality: str | None,
    region: str | None,
    country: str | None,
) -> str | None:
    """Build a human-readable address from Photon properties."""

    candidates = [
        name,
        properties.get("street"),
        properties.get("housenumber"),
        locality,
        region,
        country,
    ]

    parts: list[str] = []

    for candidate in candidates:
        normalized = _normalize_optional_string(
            candidate,
            max_length=DISPLAY_ADDRESS_MAX_LENGTH,
        )

        if normalized and normalized not in parts:
            parts.append(normalized)

    if not parts:
        return None

    return _normalize_optional_string(
        ", ".join(parts),
        max_length=DISPLAY_ADDRESS_MAX_LENGTH,
    )


def _first_normalized_string(
    *values: Any,
    max_length: int,
) -> str | None:
    """Return the first non-empty normalized string."""

    for value in values:
        normalized = _normalize_optional_string(
            value,
            max_length=max_length,
        )

        if normalized:
            return normalized

    return None


def _serialize_bounding_box(
    value: Any,
) -> dict | None:
    """
    Serialize a Nominatim bounding box.

    Nominatim order:
    south, north, west, east
    """

    if not isinstance(
        value,
        (list, tuple),
    ) or len(value) != 4:
        return None

    south = _to_optional_float(
        value[0]
    )
    north = _to_optional_float(
        value[1]
    )
    west = _to_optional_float(
        value[2]
    )
    east = _to_optional_float(
        value[3]
    )

    if None in {
        south,
        north,
        west,
        east,
    }:
        return None

    return {
        "south": south,
        "north": north,
        "west": west,
        "east": east,
    }


def _serialize_photon_extent(
    value: Any,
) -> dict | None:
    """
    Serialize Photon extent.

    Photon commonly returns:
    west, north, east, south
    """

    if not isinstance(
        value,
        (list, tuple),
    ) or len(value) != 4:
        return None

    west = _to_optional_float(
        value[0]
    )
    north = _to_optional_float(
        value[1]
    )
    east = _to_optional_float(
        value[2]
    )
    south = _to_optional_float(
        value[3]
    )

    if None in {
        south,
        north,
        west,
        east,
    }:
        return None

    return {
        "south": south,
        "north": north,
        "west": west,
        "east": east,
    }


def _normalize_country_code(
    value: Any,
) -> str | None:
    """Normalize an ISO alpha-2 country code."""

    normalized = _normalize_optional_string(
        value,
        max_length=COUNTRY_CODE_LENGTH,
    )

    if not normalized:
        return None

    normalized = normalized.upper()

    if (
        len(normalized) != COUNTRY_CODE_LENGTH
        or not normalized.isalpha()
    ):
        return None

    return normalized


def _normalize_string_list(
    value: Any,
    *,
    max_items: int,
    max_length: int,
) -> list[str]:
    """Normalize and deduplicate a string list."""

    if not isinstance(
        value,
        (list, tuple),
    ):
        return []

    result: list[str] = []

    for item in value:
        normalized = _normalize_optional_string(
            item,
            max_length=max_length,
        )

        if not normalized:
            continue

        if normalized in result:
            continue

        result.append(normalized)

        if len(result) >= max_items:
            break

    return result


def _format_distance(
    value: Any,
    units: Any,
) -> str | None:
    """Format route distance for mobile display."""

    distance = _to_optional_float(
        value
    )

    if distance is None:
        return None

    normalized_units = _normalize_optional_string(
        units,
        max_length=30,
    ) or "kilometers"

    suffix = "mi" if normalized_units == "miles" else "km"

    if distance < 10:
        return f"{distance:.1f} {suffix}"

    return f"{distance:.0f} {suffix}"


def _format_duration(
    value: Any,
) -> str | None:
    """Format duration seconds for mobile display."""

    seconds = _to_optional_float(
        value
    )

    if seconds is None:
        return None

    total_minutes = max(
        1,
        int(round(seconds / 60)),
    )

    if total_minutes < 60:
        return f"{total_minutes} min"

    hours = total_minutes // 60
    minutes = total_minutes % 60

    if minutes == 0:
        return f"{hours} hr"

    return f"{hours} hr {minutes} min"


def _as_dict(
    value: Any,
) -> dict:
    """Return a dictionary or an empty dictionary."""

    if isinstance(value, dict):
        return value

    return {}


def _normalize_optional_string(
    value: Any,
    *,
    max_length: int | None = None,
) -> str | None:
    """Trim optional text and apply a defensive length bound."""

    if value is None:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    if (
        max_length is not None
        and len(normalized) > max_length
    ):
        normalized = normalized[:max_length]

    return normalized


def _to_optional_float(
    value: Any,
) -> float | None:
    """Safely convert a value to float."""

    if value is None or value == "":
        return None

    try:
        return float(value)
    except (
        TypeError,
        ValueError,
    ):
        return None


def _first_available_float(
    *values: Any,
) -> float | None:
    """Return the first value that can be converted to float."""

    for value in values:
        normalized = _to_optional_float(
            value
        )

        if normalized is not None:
            return normalized

    return None


def _to_optional_int(
    value: Any,
) -> int | None:
    """Safely convert a value to int."""

    if value is None or value == "":
        return None

    try:
        return int(value)
    except (
        TypeError,
        ValueError,
    ):
        return None
