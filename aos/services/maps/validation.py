"""Strict, bounded validation for the global Maps domain."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any

from aos.api.maps.constants import (
	AUTOCOMPLETE_DEFAULT_LIMIT,
	AUTOCOMPLETE_MAX_LIMIT,
	COORDINATE_PRECISION,
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
)
from aos.services.sellers.constants import (
	LOCATION_INSTRUCTIONS_MAX_LENGTH,
	LOCATION_NAME_MAX_LENGTH,
	SELLER_MAP_POINTS_DEFAULT_ZOOM,
	SELLER_MAP_POINTS_MAX_ZOOM,
	SELLER_MAP_POINTS_MIN_ZOOM,
)

from .errors import MapsValidationError

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_COUNTRY_CODE_RE = re.compile(r"^[A-Za-z]{2}$")
_SCRIPT_SCHEME_RE = re.compile(r"(?:javascript|data|vbscript)\s*:", re.IGNORECASE)
_MAX_ROUTE_JSON_BYTES = 64 * 1024
_MAX_VIEWPORT_SPAN_DEGREES = 30.0
_HIGH_ZOOM_MAX_SPAN_DEGREES = 2.0

_SEARCH_KEYS = frozenset({"q", "limit", "country_code", "language"})
_AUTOCOMPLETE_KEYS = _SEARCH_KEYS | frozenset({"latitude", "longitude"})
_REVERSE_KEYS = frozenset({"latitude", "longitude", "language"})
_ROUTE_KEYS = frozenset({"locations", "destination_seller_id", "costing", "units", "language"})
_REFRESH_KEYS = frozenset(
	{"current_latitude", "current_longitude", "destination_seller_id", "costing", "units", "language"}
)
_MAP_POINTS_KEYS = frozenset(
	{"north", "south", "east", "west", "zoom", "seller_type", "business_category", "is_verified"}
)
_GET_LOCATION_KEYS = frozenset({"seller_id"})
_SET_LOCATION_KEYS = frozenset(
	{"latitude", "longitude", "location_name", "location_instructions", "expected_version"}
)
_REMOVE_LOCATION_KEYS = frozenset({"expected_version"})
_LOCATION_POINT_KEYS = frozenset({"latitude", "longitude"})


def validate_search_request(
	payload: dict[str, Any],
	*,
	default_limit: int = SEARCH_DEFAULT_LIMIT,
	max_limit: int = SEARCH_MAX_LIMIT,
) -> dict[str, Any]:
	request = _mapping(payload)
	_reject_unknown(request, _SEARCH_KEYS)
	query = normalize_required_text(request.get("q"), label="Search query", max_length=SEARCH_MAX_QUERY_LENGTH)
	if len(query) < SEARCH_MIN_QUERY_LENGTH:
		raise MapsValidationError(
			f"Search query must contain at least {SEARCH_MIN_QUERY_LENGTH} characters.",
			code="INVALID_SEARCH_QUERY",
		)
	return {
		"query": query,
		"limit": validate_limit(request.get("limit"), default=default_limit, maximum=max_limit),
		"country_code": validate_country_code(request.get("country_code")),
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
	latitude = validate_optional_latitude(request.get("latitude"))
	longitude = validate_optional_longitude(request.get("longitude"))
	if (latitude is None) != (longitude is None):
		raise MapsValidationError("Both latitude and longitude are required when using location bias.")
	return {**base, "latitude": latitude, "longitude": longitude}


def validate_reverse_geocode_request(payload: dict[str, Any]) -> dict[str, Any]:
	request = _mapping(payload)
	_reject_unknown(request, _REVERSE_KEYS)
	return {
		"latitude": validate_latitude(request.get("latitude")),
		"longitude": validate_longitude(request.get("longitude")),
		"language": validate_language(request.get("language")),
	}


def validate_route_request(payload: dict[str, Any]) -> dict[str, Any]:
	request = _mapping(payload)
	_reject_unknown(request, _ROUTE_KEYS)
	seller_reference = normalize_optional_text(
		request.get("destination_seller_id"), label="destination_seller_id", max_length=160
	)
	locations = validate_route_locations(request.get("locations"), allow_missing_destination=bool(seller_reference))
	return {
		"locations": locations,
		"destination_seller": seller_reference,
		"costing": validate_choice(
			request.get("costing"), label="costing", default=ROUTE_DEFAULT_COSTING, allowed=ROUTE_ALLOWED_COSTINGS
		),
		"units": validate_choice(
			request.get("units"), label="units", default=ROUTE_DEFAULT_UNITS, allowed=ROUTE_ALLOWED_UNITS
		),
		"language": validate_language(request.get("language")) or ROUTE_DEFAULT_LANGUAGE,
	}


def validate_refresh_route_request(payload: dict[str, Any]) -> dict[str, Any]:
	request = _mapping(payload)
	_reject_unknown(request, _REFRESH_KEYS)
	return {
		"locations": [
			{
				"latitude": validate_latitude(request.get("current_latitude")),
				"longitude": validate_longitude(request.get("current_longitude")),
			}
		],
		"destination_seller": normalize_required_text(
			request.get("destination_seller_id"), label="destination_seller_id", max_length=160
		),
		"costing": validate_choice(
			request.get("costing"), label="costing", default=ROUTE_DEFAULT_COSTING, allowed=ROUTE_ALLOWED_COSTINGS
		),
		"units": validate_choice(
			request.get("units"), label="units", default=ROUTE_DEFAULT_UNITS, allowed=ROUTE_ALLOWED_UNITS
		),
		"language": validate_language(request.get("language")) or ROUTE_DEFAULT_LANGUAGE,
	}


def validate_route_locations(locations: Any, *, allow_missing_destination: bool = False) -> list[dict[str, float]]:
	parsed = _parse_locations(locations)
	minimum = 1 if allow_missing_destination else 2
	if len(parsed) < minimum:
		raise MapsValidationError(
			"An origin location is required." if allow_missing_destination else "At least two route locations are required."
		)
	if len(parsed) > ROUTE_MAX_LOCATIONS:
		raise MapsValidationError(f"A route cannot contain more than {ROUTE_MAX_LOCATIONS} locations.")
	normalized: list[dict[str, float]] = []
	for index, raw in enumerate(parsed):
		if not isinstance(raw, dict):
			raise MapsValidationError(f"Route location {index + 1} must be an object.")
		_reject_unknown(raw, _LOCATION_POINT_KEYS, label=f"Route location {index + 1}")
		point = {
			"latitude": validate_latitude(raw.get("latitude")),
			"longitude": validate_longitude(raw.get("longitude")),
		}
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
	zoom = validate_integer(
		request.get("zoom"),
		label="zoom",
		default=SELLER_MAP_POINTS_DEFAULT_ZOOM,
		minimum=SELLER_MAP_POINTS_MIN_ZOOM,
		maximum=SELLER_MAP_POINTS_MAX_ZOOM,
	)
	lat_span = north - south
	lon_span = _longitude_span(west=west, east=east)
	if lat_span > _MAX_VIEWPORT_SPAN_DEGREES or lon_span > _MAX_VIEWPORT_SPAN_DEGREES:
		raise MapsValidationError("Viewport is too large.")
	if zoom >= 14 and (lat_span > _HIGH_ZOOM_MAX_SPAN_DEGREES or lon_span > _HIGH_ZOOM_MAX_SPAN_DEGREES):
		raise MapsValidationError("Viewport is too large for this zoom level.")
	seller_type = normalize_optional_text(request.get("seller_type"), label="seller_type", max_length=40)
	if seller_type and seller_type not in {"Individual", "Business"}:
		raise MapsValidationError("seller_type must be Individual or Business.")
	return {
		"north": north,
		"south": south,
		"east": east,
		"west": west,
		"crosses_antimeridian": east < west,
		"zoom": zoom,
		"seller_type": seller_type,
		"business_category": normalize_optional_text(
			request.get("business_category"), label="business_category", max_length=140
		),
		"is_verified": parse_optional_boolean(request.get("is_verified"), field_label="is_verified"),
	}


def validate_get_seller_location_request(payload: dict[str, Any]) -> dict[str, Any]:
	request = _mapping(payload)
	_reject_unknown(request, _GET_LOCATION_KEYS)
	return {
		"seller_reference": normalize_optional_text(request.get("seller_id"), label="seller_id", max_length=160)
	}


def validate_set_seller_location_request(payload: dict[str, Any]) -> dict[str, Any]:
	request = _mapping(payload)
	_reject_unknown(request, _SET_LOCATION_KEYS)
	return {
		"latitude": validate_latitude(request.get("latitude")),
		"longitude": validate_longitude(request.get("longitude")),
		"location_name": normalize_optional_public_text(
			request.get("location_name"), label="Location name", max_length=LOCATION_NAME_MAX_LENGTH
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
	return validate_integer(value, label="expected_version", default=0, minimum=0, maximum=2_147_483_647)


def validate_optional_latitude(value: Any) -> float | None:
	return None if value is None or value == "" else validate_latitude(value)


def validate_optional_longitude(value: Any) -> float | None:
	return None if value is None or value == "" else validate_longitude(value)


def validate_latitude(value: Any) -> float:
	return validate_coordinate(value=value, label="Latitude", minimum=LATITUDE_MIN, maximum=LATITUDE_MAX)


def validate_longitude(value: Any) -> float:
	return validate_coordinate(value=value, label="Longitude", minimum=LONGITUDE_MIN, maximum=LONGITUDE_MAX)


def validate_coordinate(*, value: Any, label: str, minimum: float, maximum: float) -> float:
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


def validate_limit(value: Any, *, default: int, maximum: int) -> int:
	return validate_integer(value, label="limit", default=default, minimum=1, maximum=maximum, clamp_maximum=True)


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
	if isinstance(value, bool) or isinstance(value, (dict, list, tuple, set, float)):
		raise MapsValidationError(f"{label} must be an integer.")
	try:
		parsed = int(str(value).strip())
	except (TypeError, ValueError) as exc:
		raise MapsValidationError(f"{label} must be an integer.") from exc
	if parsed < minimum:
		raise MapsValidationError(f"{label} must be at least {minimum}.")
	if parsed > maximum:
		if clamp_maximum:
			return maximum
		raise MapsValidationError(f"{label} must not exceed {maximum}.")
	return parsed


def validate_choice(value: Any, *, label: str, default: str, allowed: set[str]) -> str:
	if value is None or value == "":
		return default
	if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
		raise MapsValidationError(f"{label} is invalid.")
	normalized = str(value).strip().lower()
	if normalized not in allowed:
		raise MapsValidationError(f"{label} must be one of: {', '.join(sorted(allowed))}.")
	return normalized


def validate_language(value: Any) -> str | None:
	normalized = normalize_optional_text(value, label="language", max_length=ROUTE_LANGUAGE_MAX_LENGTH)
	if not normalized:
		return None
	if not _LANGUAGE_RE.fullmatch(normalized):
		raise MapsValidationError("language must be a valid BCP-47-style language tag.")
	return normalized


def validate_country_code(value: Any) -> str | None:
	normalized = normalize_optional_text(value, label="country_code", max_length=2)
	if not normalized:
		return None
	if not _COUNTRY_CODE_RE.fullmatch(normalized):
		raise MapsValidationError("country_code must be a two-letter ISO country code.")
	return normalized.upper()


def parse_boolean(value: Any, *, default: bool, field_label: str) -> bool:
	if value is None or value == "":
		return default
	parsed = parse_optional_boolean(value, field_label=field_label)
	return default if parsed is None else parsed


def parse_optional_boolean(value: Any, *, field_label: str) -> bool | None:
	if value is None or value == "":
		return None
	if isinstance(value, bool):
		return value
	if isinstance(value, int) and value in {0, 1}:
		return bool(value)
	normalized = str(value).strip().lower()
	if normalized in {"1", "true"}:
		return True
	if normalized in {"0", "false"}:
		return False
	raise MapsValidationError(f"{field_label} must be true or false.")


def normalize_required_text(value: Any, *, label: str, max_length: int) -> str:
	normalized = normalize_optional_text(value, label=label, max_length=max_length)
	if not normalized:
		raise MapsValidationError(f"{label} is required.")
	return normalized


def normalize_optional_text(value: Any, *, label: str, max_length: int) -> str | None:
	if value is None or value == "":
		return None
	if isinstance(value, (dict, list, tuple, set, bytes, bytearray)):
		raise MapsValidationError(f"{label} must be text.")
	normalized = " ".join(unicodedata.normalize("NFKC", str(value)).strip().split())
	if not normalized:
		return None
	if _CONTROL_RE.search(normalized):
		raise MapsValidationError(f"{label} contains unsupported control characters.")
	if len(normalized) > max_length:
		raise MapsValidationError(f"{label} must not exceed {max_length} characters.")
	return normalized


def normalize_optional_public_text(value: Any, *, label: str, max_length: int) -> str | None:
	normalized = normalize_optional_text(value, label=label, max_length=max_length)
	if normalized and _SCRIPT_SCHEME_RE.search(normalized):
		raise MapsValidationError(f"{label} contains an unsupported value.")
	return normalized


def _mapping(value: Any) -> dict[str, Any]:
	if not isinstance(value, dict):
		raise MapsValidationError("Request payload must be an object.")
	return dict(value)


def _reject_unknown(payload: dict[str, Any], allowed: frozenset[str], *, label: str = "Request") -> None:
	unknown = sorted(str(key) for key in payload if key not in allowed)
	if unknown:
		raise MapsValidationError(f"{label} contains unsupported fields: {', '.join(unknown)}.")


def _parse_locations(value: Any) -> list[Any]:
	if value is None or value == "":
		return []
	if isinstance(value, str):
		if len(value.encode("utf-8")) > _MAX_ROUTE_JSON_BYTES:
			raise MapsValidationError("locations payload is too large.")
		try:
			value = json.loads(value)
		except json.JSONDecodeError as exc:
			raise MapsValidationError("locations must be a JSON array.") from exc
	if not isinstance(value, list):
		raise MapsValidationError("locations must be an array.")
	return value


def _longitude_span(*, west: float, east: float) -> float:
	return east - west if east >= west else (180.0 - west) + (east + 180.0)
