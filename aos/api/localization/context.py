from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.locale_hints import accept_language_hint, geo_country_hint
from aos.api.shared.responses import fail, ok
from aos.services.localization import resolve_guest_context, serialize_context
from aos.services.user_preference_service import get_user_preference

from .constants import RESOLVE_LOCALE_CONTEXT_LIMIT_PER_MINUTE
from .throttle import localization_rate_limit
from .validation import reject_unknown_fields


def resolve_effective_locale_context(*, country: Any = None, currency: Any = None, language: Any = None):
	"""Resolve stored authenticated state or a guest/bootstrap request context."""

	user = current_user()
	if user and user != "Guest":
		if any(value not in (None, "") for value in (country, currency, language)):
			return None, fail(
				"Authenticated locale context cannot be overridden by request values.",
				error="LOCALIZATION_OVERRIDE_NOT_ALLOWED",
			)
		preference = get_user_preference(user)
		if not preference:
			return None, fail("User preference not configured.", error="PREFERENCE_MISSING")
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
	return resolve_guest_context(
		country=country,
		currency=currency,
		language=language,
		geo_country=geo_country_hint(),
		accept_language=accept_language_hint(),
	)


def resolve_locale_context_impl(**kwargs):
	"""Resolve the canonical effective country, currency, and language."""

	invalid = reject_unknown_fields(kwargs, allowed={"country", "currency", "language"})
	if invalid:
		return invalid
	limited = localization_rate_limit(
		endpoint="context",
		limit=RESOLVE_LOCALE_CONTEXT_LIMIT_PER_MINUTE,
		message="Too many requests. Please try again later.",
	)
	if limited:
		return limited
	try:
		context, error = resolve_effective_locale_context(
			country=kwargs.get("country"),
			currency=kwargs.get("currency"),
			language=kwargs.get("language"),
		)
		if error:
			return error
		return ok("Locale context resolved.", data=serialize_context(context))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "AOS Resolve Locale Context Failed")
		return fail("Failed to resolve locale context.", error="INTERNAL_ERROR")
