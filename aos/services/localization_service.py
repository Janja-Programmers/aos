"""Canonical localization master-data, validation, and context service.

This module is request-agnostic. API handlers supply request/header hints while
accounts, auth, DocTypes, and business services share the same canonical
validation, serialization, and cache contract.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

import frappe

from aos.api.shared.responses import fail
from aos.utils.aos_settings import get_aos_settings_snapshot

LOCALIZATION_CACHE_SCHEMA = "v3"
LOCALIZATION_CACHE_TTL_SECONDS = 300
LOCALIZATION_DEFAULTS_CACHE_KEY = "aos:localization:defaults:v3"
LOCALIZATION_BUNDLE_CACHE_KEY = "aos:localization:bundle:v3"
LOCALE_BUNDLE_SCHEMA_VERSION = "1.1"

MAX_COUNTRY_INPUT_LENGTH = 140
MAX_CURRENCY_INPUT_LENGTH = 16
MAX_LANGUAGE_INPUT_LENGTH = 140
MAX_ACCEPT_LANGUAGE_LENGTH = 512
MAX_ACCEPT_LANGUAGE_ITEMS = 20

_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$")


def _clean(value: Any, *, max_length: int | None = None) -> str:
	text = value.strip() if isinstance(value, str) else ""
	if max_length is not None and len(text) > max_length:
		return ""
	return text


def _value(source: Any, fieldname: str) -> Any:
	if isinstance(source, dict):
		return source.get(fieldname)
	return getattr(source, fieldname, None)


def _meta_has_field(doctype: str, fieldname: str) -> bool:
	return bool(frappe.get_meta(doctype).has_field(fieldname))


def _cache_get(key: str):
	try:
		cached = frappe.cache().get_value(key)
		if isinstance(cached, dict) and cached.get("_schema") == LOCALIZATION_CACHE_SCHEMA:
			payload = copy.deepcopy(cached)
			payload.pop("_schema", None)
			return payload
	except Exception:
		pass
	return None


def _cache_set(key: str, payload: dict[str, Any]) -> None:
	try:
		frappe.cache().set_value(
			key,
			{"_schema": LOCALIZATION_CACHE_SCHEMA, **copy.deepcopy(payload)},
			expires_in_sec=LOCALIZATION_CACHE_TTL_SECONDS,
		)
	except Exception:
		pass


def clear_localization_cache(*_args: Any, **_kwargs: Any) -> None:
	"""Invalidate cached defaults and locale master data."""

	try:
		cache = frappe.cache()
	except Exception:
		return

	for key in (LOCALIZATION_DEFAULTS_CACHE_KEY, LOCALIZATION_BUNDLE_CACHE_KEY):
		try:
			cache.delete_value(key)
		except Exception:
			pass


def localization_master_changed(_doc: Any = None, _method: str | None = None) -> None:
	"""Frappe document-event adapter for core localization master changes."""

	clear_localization_cache()


def country_code_to_flag(code: str | None) -> str | None:
	code = _clean(code, max_length=2).upper()
	if len(code) != 2 or not code.isalpha():
		return None
	return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


def validate_country(value: Any, *, required: bool = True):
	value = _clean(value, max_length=MAX_COUNTRY_INPUT_LENGTH)
	if not value:
		if required:
			return None, fail("Country is required.", error="INVALID_COUNTRY", data={"field": "country"})
		return None, None

	name = frappe.db.get_value("Country", value, "name")
	if not name and len(value) == 2 and value.isalpha():
		name = frappe.db.get_value("Country", {"code": value.upper()}, "name")
	if not name:
		return None, fail("Invalid country.", error="INVALID_COUNTRY", data={"field": "country"})
	return str(name), None


def validate_currency(value: Any, *, required: bool = True):
	value = _clean(value, max_length=MAX_CURRENCY_INPUT_LENGTH).upper()
	if not value:
		if required:
			return None, fail("Currency is required.", error="INVALID_CURRENCY", data={"field": "currency"})
		return None, None

	fields = ["name"]
	if _meta_has_field("Currency", "enabled"):
		fields.append("enabled")
	row = frappe.db.get_value("Currency", value, fields, as_dict=True)
	if not row:
		return None, fail("Invalid currency.", error="INVALID_CURRENCY", data={"field": "currency"})
	if "enabled" in row and not int(row.enabled or 0):
		return None, fail("Currency is disabled.", error="DISABLED_CURRENCY", data={"field": "currency"})
	return str(row.name), None


def _language_lookup(value: str):
	fields = ["name", "language_name", "language_code"]
	if _meta_has_field("Language", "enabled"):
		fields.append("enabled")
	if _meta_has_field("Language", "flag"):
		fields.append("flag")

	row = frappe.db.get_value("Language", value, fields, as_dict=True)
	if not row:
		row = frappe.db.get_value("Language", {"language_name": value}, fields, as_dict=True)
	if not row:
		row = frappe.db.get_value("Language", {"language_code": value.lower()}, fields, as_dict=True)
	return row


def validate_language(value: Any, *, required: bool = True):
	value = _clean(value, max_length=MAX_LANGUAGE_INPUT_LENGTH).replace("_", "-")
	if not value:
		if required:
			return None, fail("Language is required.", error="INVALID_LANGUAGE", data={"field": "language"})
		return None, None

	row = _language_lookup(value)
	if not row:
		return None, fail("Invalid language.", error="INVALID_LANGUAGE", data={"field": "language"})
	if "enabled" in row and not int(row.enabled or 0):
		return None, fail("Language is disabled.", error="DISABLED_LANGUAGE", data={"field": "language"})
	return str(row.name), None


def _canonical_language_tag(tag: str) -> str | None:
	tag = str(tag or "").strip().replace("_", "-")
	if not _LANGUAGE_TAG_RE.fullmatch(tag):
		return None

	parts = tag.split("-")
	canonical = [parts[0].lower()]
	for part in parts[1:]:
		if len(part) == 2 and part.isalpha():
			canonical.append(part.upper())
		elif len(part) == 4 and part.isalpha():
			canonical.append(part.title())
		else:
			canonical.append(part.lower())
	return "-".join(canonical)


def accept_language_candidates(header: str | None) -> list[str]:
	"""Return bounded quality-ordered exact tags, then primary fallbacks."""

	raw_header = str(header or "")[:MAX_ACCEPT_LANGUAGE_LENGTH]
	parsed: list[tuple[float, int, str]] = []

	for position, item in enumerate(raw_header.split(",")[:MAX_ACCEPT_LANGUAGE_ITEMS]):
		parts = [part.strip() for part in item.split(";") if part.strip()]
		canonical = _canonical_language_tag(parts[0] if parts else "")
		if not canonical or canonical == "*":
			continue

		quality = 1.0
		for parameter in parts[1:]:
			if parameter.lower().startswith("q="):
				try:
					quality = float(parameter[2:])
				except ValueError:
					quality = 0.0
				if quality < 0.0 or quality > 1.0:
					quality = 0.0
				break

		if quality > 0.0:
			parsed.append((quality, position, canonical))

	ordered = sorted(parsed, key=lambda row: (-row[0], row[1]))
	result: list[str] = []
	seen: set[str] = set()

	for _quality, _position, tag in ordered:
		key = tag.casefold()
		if key not in seen:
			seen.add(key)
			result.append(tag)

	for _quality, _position, tag in ordered:
		primary = tag.split("-", 1)[0]
		key = primary.casefold()
		if primary and key not in seen:
			seen.add(key)
			result.append(primary)

	return result


def resolve_accept_language(header: str | None) -> str | None:
	for candidate in accept_language_candidates(header):
		language, error = validate_language(candidate, required=False)
		if language and not error:
			return language
	return None


def resolve_guest_context(
	*,
	country: Any = None,
	currency: Any = None,
	language: Any = None,
	geo_country: str | None = None,
	accept_language: str | None = None,
):
	"""Resolve independent guest/bootstrap values from inputs, hints, defaults."""

	defaults, error = get_default_preferences()
	if error:
		return None, error

	resolved = dict(defaults)
	sources = {field: "default" for field in resolved}
	validators: tuple[tuple[str, Any, Callable[..., Any]], ...] = (
		("country", country, validate_country),
		("currency", currency, validate_currency),
		("language", language, validate_language),
	)

	for field, value, validator in validators:
		if value not in (None, ""):
			resolved[field], error = validator(value)
			if error:
				return None, error
			sources[field] = "request"

	if country in (None, "") and geo_country:
		geo_value, geo_error = validate_country(geo_country, required=False)
		if geo_value and not geo_error:
			resolved["country"] = geo_value
			sources["country"] = "geoip"

	if language in (None, "") and accept_language:
		accepted = resolve_accept_language(accept_language)
		if accepted:
			resolved["language"] = accepted
			sources["language"] = "accept_language"

	return {**resolved, "sources": sources}, None


def serialize_country(value: str) -> dict[str, Any]:
	row = frappe.db.get_value("Country", value, ["name", "code"], as_dict=True) or {}
	name = row.get("name") or value
	code = (row.get("code") or "").upper() or None
	return {"id": name, "name": name, "code": code, "flag": country_code_to_flag(code)}


def serialize_currency(
	value: str,
	*,
	include_state: bool = False,
	default: str | None = None,
) -> dict[str, Any]:
	fields = ["name", "symbol"]
	if _meta_has_field("Currency", "currency_name"):
		fields.append("currency_name")
	row = frappe.db.get_value("Currency", value, fields, as_dict=True) or {}
	result = {
		"id": value,
		"code": value,
		"symbol": row.get("symbol") or None,
		"name": row.get("currency_name") or value,
	}
	if include_state:
		result.update({"enabled": True, "is_default": value == default})
	return result


def serialize_language(
	value: str,
	*,
	include_state: bool = False,
	default: str | None = None,
) -> dict[str, Any]:
	row = _language_lookup(value) or {}
	canonical_id = row.get("name") or value
	result = {
		"id": canonical_id,
		"code": (row.get("language_code") or canonical_id or "").lower(),
		"name": row.get("language_name") or value,
		"flag": row.get("flag") or None,
	}
	if include_state:
		result.update({"enabled": True, "is_default": canonical_id == default})
	return result


def get_default_preferences(*, use_cache: bool = True):
	if use_cache:
		cached = _cache_get(LOCALIZATION_DEFAULTS_CACHE_KEY)
		if cached:
			return cached, None

	settings = get_aos_settings_snapshot()
	values = {
		"country": settings.default_country,
		"currency": settings.default_currency,
		"language": settings.default_language,
	}
	validators = {
		"country": validate_country,
		"currency": validate_currency,
		"language": validate_language,
	}
	resolved: dict[str, str] = {}

	for field, validator in validators.items():
		resolved[field], error = validator(values[field])
		if error:
			return None, fail(
				f"System default {field} is missing or invalid.",
				error="CONFIG_ERROR",
				data={"field": field},
			)

	_cache_set(LOCALIZATION_DEFAULTS_CACHE_KEY, resolved)
	return dict(resolved), None


def get_all_countries() -> list[dict[str, Any]]:
	rows = frappe.get_all(
		"Country",
		fields=["name", "code"],
		order_by="name asc",
		limit_page_length=0,
	)
	return [
		{
			"id": row.name,
			"name": row.name,
			"code": (row.code or "").upper() or None,
			"flag": country_code_to_flag(row.code),
		}
		for row in rows
	]


def get_enabled_currencies(default: str | None = None) -> list[dict[str, Any]]:
	fields = ["name", "symbol"]
	if _meta_has_field("Currency", "currency_name"):
		fields.append("currency_name")
	filters = {}
	if _meta_has_field("Currency", "enabled"):
		fields.append("enabled")
		filters["enabled"] = 1

	rows = frappe.get_all(
		"Currency",
		filters=filters,
		fields=fields,
		order_by="name asc",
		limit_page_length=0,
	)
	return [
		{
			"id": row.name,
			"code": row.name,
			"symbol": row.get("symbol") or None,
			"name": row.get("currency_name") or row.name,
			"enabled": True,
			"is_default": row.name == default,
		}
		for row in rows
	]


def get_enabled_languages(default: str | None = None) -> list[dict[str, Any]]:
	fields = ["name", "language_name", "language_code"]
	if _meta_has_field("Language", "flag"):
		fields.append("flag")
	filters = {}
	if _meta_has_field("Language", "enabled"):
		fields.append("enabled")
		filters["enabled"] = 1

	rows = frappe.get_all(
		"Language",
		filters=filters,
		fields=fields,
		order_by="language_name asc, name asc",
		limit_page_length=0,
	)
	return [
		{
			"id": row.name,
			"code": (row.language_code or row.name or "").lower(),
			"name": row.language_name or row.name,
			"flag": row.get("flag") or None,
			"enabled": True,
			"is_default": row.name == default,
		}
		for row in rows
	]


def get_locale_bundle_payload(*, use_cache: bool = True):
	"""Return the stable public locale bundle with bounded cache duration."""

	if use_cache:
		cached = _cache_get(LOCALIZATION_BUNDLE_CACHE_KEY)
		if cached:
			return cached, None

	defaults, error = get_default_preferences(use_cache=use_cache)
	if error:
		return None, error

	payload = {
		"schema_version": LOCALE_BUNDLE_SCHEMA_VERSION,
		"cache_ttl_seconds": LOCALIZATION_CACHE_TTL_SECONDS,
		"countries": get_all_countries(),
		"currencies": get_enabled_currencies(defaults["currency"]),
		"languages": get_enabled_languages(defaults["language"]),
		"defaults": dict(defaults),
	}
	_cache_set(LOCALIZATION_BUNDLE_CACHE_KEY, payload)
	return copy.deepcopy(payload), None


def serialize_preference(
	preference: Any,
	*,
	is_country_locked: bool = False,
) -> dict[str, Any]:
	return {
		"country": serialize_country(_value(preference, "country")),
		"currency": serialize_currency(_value(preference, "currency")),
		"language": serialize_language(_value(preference, "language")),
		"is_country_locked": bool(is_country_locked),
	}


def serialize_context(context: dict[str, Any]) -> dict[str, Any]:
	return {
		"schema_version": LOCALE_BUNDLE_SCHEMA_VERSION,
		"country": serialize_country(context["country"]),
		"currency": serialize_currency(context["currency"]),
		"language": serialize_language(context["language"]),
		"sources": dict(context["sources"]),
	}
