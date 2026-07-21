from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.localization_service import LOCALE_BUNDLE_SCHEMA_VERSION, serialize_country

from .constants import (
	GET_LOCATIONS_LIMIT_PER_MINUTE_PER_IP,
	LOCATIONS_DEFAULT_LIMIT,
	LOCATIONS_MAX_LIMIT,
	LOCATIONS_MAX_OFFSET,
	LOCATIONS_SEARCH_MAX_LENGTH,
)
from .context import resolve_effective_preference_context


def _parse_int(
	value: Any,
	*,
	field: str,
	default: int,
	minimum: int,
	maximum: int,
):
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


def _clean_search(value: Any):
	if value in (None, ""):
		return "", None
	if not isinstance(value, str):
		return None, fail(
			"Search query must be text.",
			error="INVALID_SEARCH_QUERY",
			data={"field": "q"},
		)
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


def get_locations_impl(**kwargs):
	"""Return active locations using stable ordering and offset pagination."""

	limited = rate_limit(
		key=rate_limit_key("localization", "locations", "ip", request_ip()),
		ttl_seconds=60,
		limit=GET_LOCATIONS_LIMIT_PER_MINUTE_PER_IP,
		message="Too many requests. Please try again shortly.",
	)
	if limited:
		return limited

	limit, error = _parse_int(
		kwargs.get("limit"),
		field="limit",
		default=LOCATIONS_DEFAULT_LIMIT,
		minimum=1,
		maximum=LOCATIONS_MAX_LIMIT,
	)
	if error:
		return error

	offset, error = _parse_int(
		kwargs.get("offset", kwargs.get("start")),
		field="offset",
		default=0,
		minimum=0,
		maximum=LOCATIONS_MAX_OFFSET,
	)
	if error:
		return error

	search, error = _clean_search(kwargs.get("q", kwargs.get("search")))
	if error:
		return error

	try:
		context, error = resolve_effective_preference_context(country=kwargs.get("country"))
		if error:
			return error
		country = context["country"]

		filters: dict[str, Any] = {"country": country, "is_active": 1}
		if search:
			filters["location"] = ["like", f"%{search}%"]

		rows = frappe.get_all(
			"AOS Location",
			filters=filters,
			fields=["name", "location", "country", "sort_order"],
			order_by="sort_order asc, location asc, name asc",
			limit_start=offset,
			limit_page_length=limit + 1,
		)
		has_more = len(rows) > limit
		visible_rows = rows[:limit]
		locations = [
			{
				"id": row.name,
				"name": row.location,
				"country": row.country,
				"sort_order": row.sort_order or 0,
			}
			for row in visible_rows
		]

		return ok(
			"Locations fetched.",
			data={
				"schema_version": LOCALE_BUNDLE_SCHEMA_VERSION,
				"country": serialize_country(country),
				"locations": locations,
				"pagination": {
					"limit": limit,
					"offset": offset,
					"returned": len(locations),
					"has_more": has_more,
					"next_offset": offset + limit if has_more else None,
				},
			},
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "AOS Get Locations Failed")
		return fail("Failed to fetch locations.", error="INTERNAL_ERROR")
