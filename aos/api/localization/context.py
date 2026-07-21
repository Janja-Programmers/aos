from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.locale_hints import accept_language_hint, geo_country_hint
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.localization_service import resolve_guest_context, serialize_context
from aos.services.user_preference_service import get_user_preference

from .constants import RESOLVE_LOCALE_CONTEXT_LIMIT_PER_MIN_PER_IP


def resolve_effective_preference_context(
	*,
	country: Any = None,
	currency: Any = None,
	language: Any = None,
):
	"""Resolve authenticated stored state or a guest request context."""

	user = current_user()
	if user and user != "Guest":
		preference = get_user_preference(user)
		if not preference:
			return None, fail("User preference not configured.", error="CONFIG_ERROR")
		return {
			"country": preference.country,
			"currency": preference.currency,
			"language": preference.language,
			"sources": {
				"country": "user_preference",
				"currency": "user_preference",
				"language": "user_preference",
			},
		}, None

	return resolve_guest_preference_context(
		country=country,
		currency=currency,
		language=language,
	)


def resolve_guest_preference_context(
	*,
	country: Any = None,
	currency: Any = None,
	language: Any = None,
):
	"""Resolve guest/auth-bootstrap values from inputs, headers, and defaults."""

	return resolve_guest_context(
		country=country,
		currency=currency,
		language=language,
		geo_country=geo_country_hint(),
		accept_language=accept_language_hint(),
	)


def resolve_preference_context_impl(**kwargs):
	"""Resolve the effective country, currency, and language for this request."""

	limited = rate_limit(
		key=rate_limit_key("localization", "context", "ip", request_ip()),
		ttl_seconds=60,
		limit=RESOLVE_LOCALE_CONTEXT_LIMIT_PER_MIN_PER_IP,
		message="Too many requests. Please try again later.",
	)
	if limited:
		return limited

	try:
		context, error = resolve_effective_preference_context(
			country=kwargs.get("country"),
			currency=kwargs.get("currency"),
			language=kwargs.get("language"),
		)
		if error:
			return error
		return ok("Preference context resolved.", data=serialize_context(context))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "AOS Resolve Preference Context Failed")
		return fail("Failed to resolve preference context.", error="INTERNAL_ERROR")


# Clearer public naming while the v1 compatibility wrapper remains available.
resolve_locale_context_impl = resolve_preference_context_impl
