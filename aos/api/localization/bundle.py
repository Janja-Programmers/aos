from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.localization_service import get_locale_bundle_payload

from .constants import LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP


def get_locale_bundle_impl(**_kwargs):
	"""Return cached, validated localization master data and defaults."""

	limited = rate_limit(
		key=rate_limit_key("localization", "bundle", "ip", request_ip()),
		ttl_seconds=60,
		limit=LOCALE_BUNDLE_LIMIT_PER_MIN_PER_IP,
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
