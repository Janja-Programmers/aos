from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.localization import get_locations_page

from .constants import GET_LOCATIONS_LIMIT_PER_MINUTE
from .context import resolve_effective_locale_context
from .throttle import localization_rate_limit
from .validation import parse_locations_request


def get_locations_impl(**kwargs):
	"""Return active country locations with stable, bounded offset pagination."""

	request, error = parse_locations_request(kwargs)
	if error:
		return error
	limited = localization_rate_limit(
		endpoint="locations",
		limit=GET_LOCATIONS_LIMIT_PER_MINUTE,
		message="Too many requests. Please try again shortly.",
	)
	if limited:
		return limited
	try:
		context, error = resolve_effective_locale_context(country=request["country"])
		if error:
			return error
		payload = get_locations_page(
			country=context["country"],
			search=request["q"],
			limit=request["limit"],
			offset=request["offset"],
		)
		return ok("Locations fetched.", data=payload)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "AOS Get Locations Failed")
		return fail("Failed to fetch locations.", error="INTERNAL_ERROR")
