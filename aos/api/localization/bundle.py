from __future__ import annotations

import frappe

from aos.api.shared.responses import fail, ok
from aos.services.localization import get_locale_bundle_payload

from .constants import LOCALE_BUNDLE_LIMIT_PER_MINUTE
from .throttle import localization_rate_limit
from .validation import reject_unknown_fields


def get_locale_bundle_impl(**kwargs):
	"""Return cached, validated localization master data and defaults."""

	invalid = reject_unknown_fields(kwargs, allowed=set())
	if invalid:
		return invalid
	limited = localization_rate_limit(
		endpoint="bundle",
		limit=LOCALE_BUNDLE_LIMIT_PER_MINUTE,
		message="Too many requests. Please try again later.",
	)
	if limited:
		return limited
	try:
		payload, error = get_locale_bundle_payload()
		if error:
			return error
		return ok("Locale bundle fetched.", data=payload)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "AOS Get Locale Bundle Failed")
		return fail("Failed to load locale bundle.", error="INTERNAL_ERROR")
