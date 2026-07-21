"""Bounded request hints used for guest localization bootstrap."""

from __future__ import annotations

import frappe

MAX_LOCALE_HEADER_LENGTH = 512


def request_header(name: str) -> str:
	"""Return one bounded request header or an empty string outside requests."""

	try:
		request = getattr(frappe.local, "request", None)
		headers = getattr(request, "headers", None)
		if not headers:
			return ""
		return str(headers.get(name) or "").strip()[:MAX_LOCALE_HEADER_LENGTH]
	except Exception:
		return ""


def geo_country_hint() -> str:
	"""Return the preferred proxy-provided country hint."""

	return request_header("CF-IPCountry") or request_header("X-Country-Code")


def accept_language_hint() -> str:
	return request_header("Accept-Language")
