"""Database access for localization master data and public locations."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import MAX_COUNTRIES, MAX_CURRENCIES, MAX_LANGUAGES


class LocalizationConfigurationError(RuntimeError):
	"""Raised when reference tables exceed safe public bootstrap bounds."""


def has_field(doctype: str, fieldname: str) -> bool:
	return bool(frappe.get_meta(doctype).has_field(fieldname))


def location_by_name(value: str):
	"""Return the canonical location row needed by domain validators."""
	return frappe.db.get_value(
		"AOS Location",
		str(value or "").strip(),
		["name", "location", "country", "is_active"],
		as_dict=True,
	)


def location_label(value: str) -> str:
	clean = str(value or "").strip()
	if not clean:
		return ""
	return str(frappe.get_cached_value("AOS Location", clean, "location") or "").strip()


def location_labels(values: list[str] | tuple[str, ...] | set[str]) -> dict[str, str]:
	clean = sorted({str(value or "").strip() for value in values if str(value or "").strip()})
	if not clean:
		return {}
	rows = frappe.get_all(
		"AOS Location",
		filters={"name": ["in", clean]},
		fields=["name", "location"],
		limit=len(clean),
	)
	return {str(row.name): str(row.location or "") for row in rows}

def country_by_name(value: str):
	return frappe.db.get_value("Country", value, ["name", "code"], as_dict=True)


def country_by_code(value: str):
	return frappe.db.get_value("Country", {"code": value.upper()}, ["name", "code"], as_dict=True)


def currency_by_name(value: str):
	fields = ["name", "symbol"]
	if has_field("Currency", "currency_name"):
		fields.append("currency_name")
	if has_field("Currency", "enabled"):
		fields.append("enabled")
	return frappe.db.get_value("Currency", value, fields, as_dict=True)


def language_by_value(value: str):
	"""Resolve only machine identifiers: Language.name or language_code."""
	fields = ["name", "language_name", "language_code"]
	if has_field("Language", "enabled"):
		fields.append("enabled")
	if has_field("Language", "flag"):
		fields.append("flag")
	row = frappe.db.get_value("Language", value, fields, as_dict=True)
	if row:
		return row
	return frappe.db.get_value("Language", {"language_code": value.lower()}, fields, as_dict=True)


def languages_matching_candidates(candidates: list[str]):
	"""Resolve a bounded Accept-Language candidate set in one database query."""
	if not candidates:
		return []
	placeholders = ", ".join(["%s"] * len(candidates))
	fields = "name, language_name, language_code"
	enabled_clause = ""
	if has_field("Language", "enabled"):
		fields += ", enabled"
		enabled_clause = "AND COALESCE(enabled, 0) = 1"
	params = tuple(candidates) + tuple(value.lower() for value in candidates)
	return frappe.db.sql(
		f"""
		SELECT {fields}
		FROM `tabLanguage`
		WHERE (
			name IN ({placeholders})
			OR LOWER(language_code) IN ({placeholders})
		)
		{enabled_clause}
		LIMIT {len(candidates) * 2}
		""",
		params,
		as_dict=True,
	)


def _bounded_rows(doctype: str, *, maximum: int, **kwargs: Any):
	rows = frappe.get_all(doctype, limit=maximum + 1, **kwargs)
	if len(rows) > maximum:
		raise LocalizationConfigurationError(f"{doctype} exceeds supported public bundle size")
	return rows


def list_countries():
	return _bounded_rows(
		"Country",
		maximum=MAX_COUNTRIES,
		fields=["name", "code"],
		order_by="name asc",
	)


def list_enabled_currencies():
	fields = ["name", "symbol"]
	filters: dict[str, Any] = {}
	if has_field("Currency", "currency_name"):
		fields.append("currency_name")
	if has_field("Currency", "enabled"):
		fields.append("enabled")
		filters["enabled"] = 1
	return _bounded_rows(
		"Currency",
		maximum=MAX_CURRENCIES,
		filters=filters,
		fields=fields,
		order_by="name asc",
	)


def list_enabled_languages():
	fields = ["name", "language_name", "language_code"]
	filters: dict[str, Any] = {}
	if has_field("Language", "flag"):
		fields.append("flag")
	if has_field("Language", "enabled"):
		fields.append("enabled")
		filters["enabled"] = 1
	return _bounded_rows(
		"Language",
		maximum=MAX_LANGUAGES,
		filters=filters,
		fields=fields,
		order_by="language_name asc, name asc",
	)


def list_active_locations(*, country: str, search: str, limit: int, offset: int):
	"""Fetch one bounded page using literal substring search and stable ordering."""
	return frappe.db.sql(
		"""
		SELECT name, location, country, sort_order
		FROM `tabAOS Location`
		WHERE country = %(country)s
		  AND is_active = 1
		  AND (%(search)s = '' OR LOCATE(%(search)s, location) > 0)
		ORDER BY sort_order ASC, location ASC
		LIMIT %(limit)s OFFSET %(offset)s
		""",
		{
			"country": country,
			"search": search,
			"limit": limit + 1,
			"offset": offset,
		},
		as_dict=True,
	)
