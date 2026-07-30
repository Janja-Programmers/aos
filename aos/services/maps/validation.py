"""Strict, bounded request validation for the Maps domain."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any, Iterable

from aos.api.maps.constants import (
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
from aos.api.sellers.constants import (
    LOCATION_INSTRUCTIONS_MAX_LENGTH,
    LOCATION_NAME_MAX_LENGTH,
    SELLER_MAP_POINTS_DEFAULT_ZOOM,
    SELLER_MAP_POINTS_MAX_ZOOM,
    SELLER_MAP_POINTS_MIN_ZOOM,
)

from .errors import MapsValidationError

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_SCRIPT_SCHEME_RE = re.compile(r"(?:javascript|data|vbscript)\s*:", re.IGNORECASE)
_MAX_ROUTE_JSON_BYTES = 64 * 1024
_MAX_VIEWPORT_SPAN_DEGREES = 12.0
_HIGH_ZOOM_MAX_SPAN_DEGREES = 1.0

_SEARCH_KEYS = frozenset(
    {"query", "q", "limit", "bounded", "country_codes", "countrycodes", "language"}
)
_AUTOCOMPLETE_KEYS = _SEARCH_KEYS | frozenset(
    {"latitude", "lat", "longitude", "lon", "lng"}
)
_REVERSE_KEYS = frozenset(
    {"latitude", "lat", "longitude", "lon", "lng", "language"}
)
_ROUTE_KEYS = frozenset(
    {
        "locations",
        "points",
        "origin_latitude",
        "origin_lat",
        "origin_longitude",
        "origin_lon",
        "origin_lng",
        "destination_latitude",
        "destination_lat",
        "destination_longitude",
        "destination_lon",
        "destination_lng",
        "destination_seller",
        "seller",
        "seller_id",
        "costing",
        "units",
        "language",
    }
)
_REFRESH_KEYS = frozenset(
    {
        "current_latitude",
        "current_lat",
        "current_longitude",
        "current_lon",
        "current_lng",
        "latitude",
        "lat",
        "longitude",
        "lon",
        "lng",
        "destination_seller",
        "seller",
        "seller_id",
        "costing",
        "units",
        "language",
    }
)
_MAP_POINTS_KEYS = frozenset(
    {
        "north",
        "south",
        "east",
        "west",
        "zoom",
        "seller_type",
        "business_category",
        "category",
        "is_verified",
    }
)
_GET_LOCATION_KEYS = frozenset({"seller", "seller_id", "id"})
_SET_LOCATION_KEYS = frozenset(
    {
        "latitude",
        "lat",
        "longitude",
        "lon",
        "lng",
        "location_name",
        "location_instructions",
        "expected_version",
    }
)
_REMOVE_LOCATION_KEYS = frozenset({"expected_version"})
_LOCATION_POINT_KEYS = frozenset({"latitude", "lat", "longitude", "lon", "lng"})


def validate_search_request(
    payload: dict[str, Any],
    *,
    default_limit: int = SEARCH_DEFAULT_LIMIT,
    max_limit: int = SEARCH_MAX_LIMIT,
) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _SEARCH_KEYS)
    query = normalize_required_text(
        _alias(request, ("query", "q"), label="query"),
        label="Search query",
        max_length=SEARCH_MAX_QUERY_LENGTH,
    )
    if len(query) < SEARCH_MIN_QUERY_LENGTH:
        raise MapsValidationError(
            f"Search query must contain at least {SEARCH_MIN_QUERY_LENGTH} characters.",
            code="INVALID_SEARCH_QUERY",
        )
    return {
        "query": query,
        "limit": validate_limit(request.get("limit"), default=default_limit, maximum=max_limit),
        "bounded": parse_boolean(
            request.get("bounded"),
            default=DEFAULT_SEARCH_BOUNDED,
            field_label="bounded",
        ),
        "country_codes": normalize_country_codes(
            _alias(request, ("country_codes", "countrycodes"), label="country_codes")
            or DEFAULT_SEARCH_COUNTRY_CODES
        ),
        "language": validate_language(request.get("language")),
    }


def validate_autocomplete_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _AUTOCOMPLETE_KEYS)
    base = validate_search_request(
        {key: value for key, value in request.items() if key in _SEARCH_KEYS},
        default_limit=AUTOCOMPLETE_DEFAULT_LIMIT,
        max_limit=AUTOCOMPLETE_MAX_LIMIT,
    )
    latitude = validate_optional_latitude(_alias(request, ("latitude", "lat"), label="latitude"))
    longitude = validate_optional_longitude(
        _alias(request, ("longitude", "lon", "lng"), label="longitude")
    )
    if (latitude is None) != (longitude is None):
        raise MapsValidationError(
            "Both latitude and longitude are required when using location bias."
        )
    if latitude is not None and longitude is not None:
        validate_supported_location(latitude=latitude, longitude=longitude, label="Location bias")
    return {**base, "latitude": latitude, "longitude": longitude}


def validate_reverse_geocode_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _REVERSE_KEYS)
    latitude = validate_latitude(_alias(request, ("latitude", "lat"), label="latitude"))
    longitude = validate_longitude(
        _alias(request, ("longitude", "lon", "lng"), label="longitude")
    )
    validate_supported_location(latitude=latitude, longitude=longitude)
    return {
        "latitude": latitude,
        "longitude": longitude,
        "language": validate_language(request.get("language")),
    }


def validate_route_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _ROUTE_KEYS)
    seller_reference = normalize_optional_text(
        _alias(
            request,
            ("destination_seller", "seller", "seller_id"),
            label="destination_seller",
        ),
        label="destination_seller",
        max_length=160,
    )
    raw_locations = _alias(request, ("locations", "points"), label="locations")
    if raw_locations is not None and seller_reference:
        raise MapsValidationError(
            "locations cannot be combined with destination_seller."
        )
    if raw_locations is None:
        raw_locations = _build_origin_destination_locations(request, seller_reference)
    locations = validate_route_locations(raw_locations, allow_missing_destination=bool(seller_reference))
    return {
        "locations": locations,
        "destination_seller": seller_reference,
        "costing": validate_choice(
            request.get("costing"),
            label="costing",
            default=ROUTE_DEFAULT_COSTING,
            allowed=ROUTE_ALLOWED_COSTINGS,
        ),
        "units": validate_choice(
            request.get("units"),
            label="units",
            default=ROUTE_DEFAULT_UNITS,
            allowed=ROUTE_ALLOWED_UNITS,
        ),
        "language": validate_language(request.get("language")) or ROUTE_DEFAULT_LANGUAGE,
    }


def validate_refresh_route_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _REFRESH_KEYS)
    seller_reference = normalize_required_text(
        _alias(
            request,
            ("destination_seller", "seller", "seller_id"),
            label="destination_seller",
        ),
        label="destination_seller",
        max_length=160,
    )
    latitude = validate_latitude(
        _alias(
            request,
            ("current_latitude", "current_lat", "latitude", "lat"),
            label="current_latitude",
        )
    )
    longitude = validate_longitude(
        _alias(
            request,
            ("current_longitude", "current_lon", "current_lng", "longitude", "lon", "lng"),
            label="current_longitude",
        )
    )
    validate_supported_location(latitude=latitude, longitude=longitude, label="Current location")
    return {
        "locations": [{"latitude": latitude, "longitude": longitude}],
        "destination_seller": seller_reference,
        "costing": validate_choice(
            request.get("costing"),
            label="costing",
            default=ROUTE_DEFAULT_COSTING,
            allowed=ROUTE_ALLOWED_COSTINGS,
        ),
        "units": validate_choice(
            request.get("units"),
            label="units",
            default=ROUTE_DEFAULT_UNITS,
            allowed=ROUTE_ALLOWED_UNITS,
        ),
        "language": validate_language(request.get("language")) or ROUTE_DEFAULT_LANGUAGE,
    }


def validate_route_locations(
    locations: Any,
    *,
    allow_missing_destination: bool = False,
) -> list[dict[str, float]]:
    parsed = _parse_locations(locations)
    minimum = 1 if allow_missing_destination else 2
    if len(parsed) < minimum:
        message = (
            "An origin location is required."
            if allow_missing_destination
            else "At least two route locations are required."
        )
        raise MapsValidationError(message)
    if len(parsed) > ROUTE_MAX_LOCATIONS:
        raise MapsValidationError(
            f"A route cannot contain more than {ROUTE_MAX_LOCATIONS} locations."
        )
    normalized: list[dict[str, float]] = []
    for index, raw in enumerate(parsed):
        if not isinstance(raw, dict):
            raise MapsValidationError(f"Route location {index + 1} must be an object.")
        _reject_unknown(raw, _LOCATION_POINT_KEYS, label=f"Route location {index + 1}")
        latitude = validate_latitude(_alias(raw, ("latitude", "lat"), label="latitude"))
        longitude = validate_longitude(
            _alias(raw, ("longitude", "lon", "lng"), label="longitude")
        )
        validate_supported_location(
            latitude=latitude,
            longitude=longitude,
            label=f"Route location {index + 1}",
        )
        point = {"latitude": latitude, "longitude": longitude}
        if normalized and normalized[-1] == point:
            raise MapsValidationError("Consecutive route locations must be different.")
        normalized.append(point)
    return normalized


def validate_map_points_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _MAP_POINTS_KEYS)
    north = validate_coordinate(value=request.get("north"), label="north", minimum=-90, maximum=90)
    south = validate_coordinate(value=request.get("south"), label="south", minimum=-90, maximum=90)
    east = validate_coordinate(value=request.get("east"), label="east", minimum=-180, maximum=180)
    west = validate_coordinate(value=request.get("west"), label="west", minimum=-180, maximum=180)
    if north <= south:
        raise MapsValidationError("north must be greater than south.")
    if east <= west:
        raise MapsValidationError("east must be greater than west.")
    if not viewport_intersects_supported_area(north=north, south=south, east=east, west=west):
        raise MapsValidationError("Viewport is outside the supported AOS Maps coverage area.")
    clipped = clamp_bbox_to_supported_area(north=north, south=south, east=east, west=west)
    if clipped is None:
        raise MapsValidationError("Viewport is outside the supported AOS Maps coverage area.")
    zoom = validate_integer(
        request.get("zoom"),
        label="zoom",
        default=SELLER_MAP_POINTS_DEFAULT_ZOOM,
        minimum=SELLER_MAP_POINTS_MIN_ZOOM,
        maximum=SELLER_MAP_POINTS_MAX_ZOOM,
    )
    lat_span = clipped["north"] - clipped["south"]
    lon_span = clipped["east"] - clipped["west"]
    if lat_span > _MAX_VIEWPORT_SPAN_DEGREES or lon_span > _MAX_VIEWPORT_SPAN_DEGREES:
        raise MapsValidationError("Viewport is too large.")
    if zoom >= 14 and (
        lat_span > _HIGH_ZOOM_MAX_SPAN_DEGREES
        or lon_span > _HIGH_ZOOM_MAX_SPAN_DEGREES
    ):
        raise MapsValidationError("Viewport is too large for this zoom level.")
    seller_type = normalize_optional_text(
        request.get("seller_type"),
        label="seller_type",
        max_length=40,
    )
    if seller_type and seller_type not in {"Individual", "Business"}:
        raise MapsValidationError("seller_type must be Individual or Business.")
    business_category = normalize_optional_text(
        _alias(request, ("business_category", "category"), label="business_category"),
        label="business_category",
        max_length=140,
    )
    return {
        **clipped,
        "zoom": zoom,
        "seller_type": seller_type,
        "business_category": business_category,
        "is_verified": parse_optional_boolean(request.get("is_verified"), field_label="is_verified"),
    }


def validate_get_seller_location_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _GET_LOCATION_KEYS)
    return {
        "seller_reference": normalize_optional_text(
            _alias(request, ("seller", "seller_id", "id"), label="seller"),
            label="seller",
            max_length=160,
        )
    }


def validate_set_seller_location_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _SET_LOCATION_KEYS)
    latitude = validate_latitude(_alias(request, ("latitude", "lat"), label="latitude"))
    longitude = validate_longitude(
        _alias(request, ("longitude", "lon", "lng"), label="longitude")
    )
    validate_supported_location(latitude=latitude, longitude=longitude, label="Seller location")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "location_name": normalize_optional_public_text(
            request.get("location_name"),
            label="Location name",
            max_length=LOCATION_NAME_MAX_LENGTH,
        ),
        "location_instructions": normalize_optional_public_text(
            request.get("location_instructions"),
            label="Location instructions",
            max_length=LOCATION_INSTRUCTIONS_MAX_LENGTH,
        ),
        "expected_version": validate_optional_version(request.get("expected_version")),
    }


def validate_remove_seller_location_request(payload: dict[str, Any]) -> dict[str, Any]:
    request = _mapping(payload)
    _reject_unknown(request, _REMOVE_LOCATION_KEYS)
    return {"expected_version": validate_optional_version(request.get("expected_version"))}


def validate_optional_version(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return validate_integer(
        value,
        label="expected_version",
        default=0,
        minimum=0,
        maximum=2_147_483_647,
    )


def validate_optional_latitude(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return validate_latitude(value)


def validate_optional_longitude(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return validate_longitude(value)


def validate_latitude(value: Any) -> float:
    return validate_coordinate(
        value=value,
        label="Latitude",
        minimum=LATITUDE_MIN,
        maximum=LATITUDE_MAX,
    )


def validate_longitude(value: Any) -> float:
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
    if value is None or value == "":
        raise MapsValidationError(f"{label} is required.")
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
        raise MapsValidationError(f"{label} must be a valid number.")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise MapsValidationError(f"{label} must be a valid number.") from exc
    if not math.isfinite(normalized):
        raise MapsValidationError(f"{label} must be a finite number.")
    if normalized < minimum or normalized > maximum:
        raise MapsValidationError(f"{label} must be between {minimum:g} and {maximum:g}.")
    return round(normalized, COORDINATE_PRECISION)


def validate_supported_location(
    *,
    latitude: float,
    longitude: float,
    label: str = "Location",
) -> None:
    if not is_supported_location(latitude=latitude, longitude=longitude):
        raise MapsValidationError(
            f"{label} is outside the supported AOS Maps coverage area.",
            code="MAP_OUTSIDE_COVERAGE",
        )


def is_supported_location(*, latitude: float, longitude: float) -> bool:
    return (
        KENYA_BBOX_SOUTH <= latitude <= KENYA_BBOX_NORTH
        and KENYA_BBOX_WEST <= longitude <= KENYA_BBOX_EAST
    )


def supported_area_bounds() -> dict[str, float]:
    return {
        "north": KENYA_BBOX_NORTH,
        "south": KENYA_BBOX_SOUTH,
        "east": KENYA_BBOX_EAST,
        "west": KENYA_BBOX_WEST,
    }


def viewport_intersects_supported_area(
    *, north: float, south: float, east: float, west: float
) -> bool:
    return not (
        north < KENYA_BBOX_SOUTH
        or south > KENYA_BBOX_NORTH
        or east < KENYA_BBOX_WEST
        or west > KENYA_BBOX_EAST
    )


def validate_supported_viewport(
    *, north: float, south: float, east: float, west: float, label: str = "Viewport"
) -> None:
    if not viewport_intersects_supported_area(
        north=north, south=south, east=east, west=west
    ):
        raise MapsValidationError(
            f"{label} is outside the supported AOS Maps coverage area.",
            code="MAP_OUTSIDE_COVERAGE",
        )


def clamp_bbox_to_supported_area(
    *, north: float, south: float, east: float, west: float
) -> dict[str, float] | None:
    if not viewport_intersects_supported_area(
        north=north, south=south, east=east, west=west
    ):
        return None
    return {
        "north": round(min(north, KENYA_BBOX_NORTH), COORDINATE_PRECISION),
        "south": round(max(south, KENYA_BBOX_SOUTH), COORDINATE_PRECISION),
        "east": round(min(east, KENYA_BBOX_EAST), COORDINATE_PRECISION),
        "west": round(max(west, KENYA_BBOX_WEST), COORDINATE_PRECISION),
    }


def validate_limit(value: Any, *, default: int, maximum: int) -> int:
    return validate_integer(
        value,
        label="limit",
        default=default,
        minimum=1,
        maximum=maximum,
        clamp_maximum=True,
    )


def validate_integer(
    value: Any,
    *,
    label: str,
    default: int,
    minimum: int,
    maximum: int,
    clamp_maximum: bool = False,
) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
        raise MapsValidationError(f"{label} must be a valid integer.")
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        raise MapsValidationError(f"{label} must be a valid integer.")
    normalized = int(text)
    if normalized < minimum:
        raise MapsValidationError(f"{label} must be at least {minimum}.")
    if normalized > maximum:
        if clamp_maximum:
            return maximum
        raise MapsValidationError(f"{label} cannot exceed {maximum}.")
    return normalized


def validate_choice(
    value: Any,
    *,
    label: str,
    default: str,
    allowed: Iterable[str],
) -> str:
    normalized = normalize_optional_text(value, label=label, max_length=40) or default
    normalized = normalized.lower()
    allowed_values = frozenset(str(item).lower() for item in allowed)
    if normalized not in allowed_values:
        raise MapsValidationError(
            f"{label} must be one of: {', '.join(sorted(allowed_values))}."
        )
    return normalized


def validate_language(value: Any) -> str | None:
    normalized = normalize_optional_text(
        value,
        label="language",
        max_length=ROUTE_LANGUAGE_MAX_LENGTH,
    )
    if normalized and not _LANGUAGE_RE.fullmatch(normalized):
        raise MapsValidationError("language must be a valid language tag.")
    return normalized


def parse_boolean(value: Any, *, default: bool, field_label: str) -> bool:
    if value is None or value == "":
        return default
    parsed = parse_optional_boolean(value, field_label=field_label)
    assert parsed is not None
    return parsed


def parse_optional_boolean(value: Any, *, field_label: str) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
        raise MapsValidationError(f"{field_label} must be true or false.")
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise MapsValidationError(f"{field_label} must be true or false.")


def normalize_country_codes(value: Any) -> str:
    normalized = normalize_optional_text(
        value,
        label="country_codes",
        max_length=32,
    )
    if not normalized:
        return DEFAULT_SEARCH_COUNTRY_CODES
    country_codes: list[str] = []
    for raw in normalized.split(","):
        code = raw.strip().lower()
        if not code:
            continue
        if len(code) != 2 or not code.isalpha():
            raise MapsValidationError(
                "country_codes must contain valid 2-letter country codes."
            )
        if code != SUPPORTED_COUNTRY_CODE.lower():
            raise MapsValidationError(
                f"Only {SUPPORTED_COUNTRY_CODE} is currently supported.",
                code="MAP_OUTSIDE_COVERAGE",
            )
        if code not in country_codes:
            country_codes.append(code)
    return ",".join(country_codes) or DEFAULT_SEARCH_COUNTRY_CODES


def normalize_required_text(
    value: Any,
    *,
    label: str,
    max_length: int,
) -> str:
    normalized = normalize_optional_text(value, label=label, max_length=max_length)
    if normalized is None:
        raise MapsValidationError(f"{label} is required.")
    return normalized


def normalize_optional_text(
    value: Any,
    *,
    label: str,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
        raise MapsValidationError(f"{label} must be text.")
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = " ".join(normalized.split())
    if not normalized:
        return None
    if _CONTROL_RE.search(normalized):
        raise MapsValidationError(f"{label} contains unsupported characters.")
    if len(normalized) > max_length:
        raise MapsValidationError(f"{label} cannot exceed {max_length} characters.")
    return normalized


def normalize_optional_public_text(
    value: Any,
    *,
    label: str,
    max_length: int,
) -> str | None:
    normalized = normalize_optional_text(value, label=label, max_length=max_length)
    if normalized is None:
        return None
    if "<" in normalized or ">" in normalized or _SCRIPT_SCHEME_RE.search(normalized):
        raise MapsValidationError(f"{label} contains unsupported content.")
    return normalized


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MapsValidationError("Request payload must be an object.")
    return dict(value)


def _reject_unknown(
    payload: dict[str, Any],
    allowed: Iterable[str],
    *,
    label: str = "Request",
) -> None:
    unknown = sorted(str(key) for key in payload if key not in set(allowed))
    if unknown:
        raise MapsValidationError(
            f"{label} contains unsupported fields: {', '.join(unknown)}."
        )


def _alias(payload: dict[str, Any], keys: tuple[str, ...], *, label: str) -> Any:
    supplied = [(key, payload.get(key)) for key in keys if key in payload and payload.get(key) not in (None, "")]
    if not supplied:
        return None
    first_value = supplied[0][1]
    first_compare = _comparable(first_value)
    for _, value in supplied[1:]:
        if _comparable(value) != first_compare:
            raise MapsValidationError(f"Conflicting aliases were supplied for {label}.")
    return first_value


def _comparable(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
        except Exception:
            return repr(value)
    return str(value).strip()


def _parse_locations(value: Any) -> list[Any]:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        if len(encoded) > _MAX_ROUTE_JSON_BYTES:
            raise MapsValidationError("locations payload is too large.")
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise MapsValidationError("locations must be valid JSON.") from exc
    if not isinstance(value, (list, tuple)):
        raise MapsValidationError("locations must be a list.")
    return list(value)


def _build_origin_destination_locations(
    payload: dict[str, Any],
    seller_reference: str | None,
) -> list[dict[str, Any]]:
    origin = {
        "latitude": _alias(payload, ("origin_latitude", "origin_lat"), label="origin_latitude"),
        "longitude": _alias(
            payload,
            ("origin_longitude", "origin_lon", "origin_lng"),
            label="origin_longitude",
        ),
    }
    locations: list[dict[str, Any]] = [origin]
    destination_latitude = _alias(
        payload,
        ("destination_latitude", "destination_lat"),
        label="destination_latitude",
    )
    destination_longitude = _alias(
        payload,
        ("destination_longitude", "destination_lon", "destination_lng"),
        label="destination_longitude",
    )
    has_destination_coordinate = destination_latitude not in (None, "") or destination_longitude not in (None, "")
    if seller_reference and has_destination_coordinate:
        raise MapsValidationError(
            "Destination coordinates cannot be combined with destination_seller."
        )
    if not seller_reference:
        locations.append(
            {"latitude": destination_latitude, "longitude": destination_longitude}
        )
    return locations
