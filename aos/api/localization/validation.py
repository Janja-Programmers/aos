"""Public Localization request validation."""

from __future__ import annotations

from typing import Any

from aos.api.shared.responses import fail

from .constants import (
	LOCATIONS_DEFAULT_LIMIT,
	LOCATIONS_MAX_LIMIT,
	LOCATIONS_MAX_OFFSET,
	LOCATIONS_SEARCH_MAX_LENGTH,
)


def reject_unknown_fields(kwargs: dict[str, Any], *, allowed: set[str]):
	unknown = sorted(set(kwargs) - allowed)
	if not unknown:
		return None
	return fail(
		"Unsupported localization request fields.",
		error="LOCALIZATION_UNKNOWN_FIELD",
		data={"fields": unknown},
	)


def parse_int(value: Any, *, field: str, default: int, minimum: int, maximum: int):
	if value in (None, ""):
		return default, None
	if isinstance(value, bool):
		return None, fail(
			f"{field.title()} must be an integer.",
			error=f"INVALID_{field.upper()}",
			data={"field": field, "minimum": minimum, "maximum": maximum},
		)
	try:
		parsed = int(str(value).strip())
	except (TypeError, ValueError):
		return None, fail(
			f"{field.title()} must be an integer.",
			error=f"INVALID_{field.upper()}",
			data={"field": field, "minimum": minimum, "maximum": maximum},
		)
	if parsed < minimum or parsed > maximum:
		return None, fail(
			f"{field.title()} is outside the allowed range.",
			error=f"INVALID_{field.upper()}",
			data={"field": field, "minimum": minimum, "maximum": maximum},
		)
	return parsed, None


def clean_search(value: Any):
	if value in (None, ""):
		return "", None
	if not isinstance(value, str):
		return None, fail("Search query must be text.", error="INVALID_SEARCH_QUERY", data={"field": "q"})
	search = " ".join(value.split())
	if len(search) > LOCATIONS_SEARCH_MAX_LENGTH:
		return None, fail(
			"Search query is too long.",
			error="INVALID_SEARCH_QUERY",
			data={"field": "q", "maximum_length": LOCATIONS_SEARCH_MAX_LENGTH},
		)
	if any(ord(character) < 32 for character in search):
		return None, fail(
			"Search query contains invalid characters.",
			error="INVALID_SEARCH_QUERY",
			data={"field": "q"},
		)
	return search, None


def parse_locations_request(kwargs: dict[str, Any]):
	error = reject_unknown_fields(kwargs, allowed={"country", "q", "limit", "offset"})
	if error:
		return None, error
	limit, error = parse_int(
		kwargs.get("limit"),
		field="limit",
		default=LOCATIONS_DEFAULT_LIMIT,
		minimum=1,
		maximum=LOCATIONS_MAX_LIMIT,
	)
	if error:
		return None, error
	offset, error = parse_int(
		kwargs.get("offset"),
		field="offset",
		default=0,
		minimum=0,
		maximum=LOCATIONS_MAX_OFFSET,
	)
	if error:
		return None, error
	search, error = clean_search(kwargs.get("q"))
	if error:
		return None, error
	return {
		"country": kwargs.get("country"),
		"q": search,
		"limit": limit,
		"offset": offset,
	}, None
