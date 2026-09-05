"""Localization domain service: defaults, bootstrap context, bundle and locations."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from aos.api.shared.responses import fail
from aos.utils.aos_settings import get_aos_settings_snapshot

from .cache import cache_get, cache_set
from .constants import (
	LOCALIZATION_BUNDLE_CACHE_KEY,
	LOCALIZATION_CACHE_TTL_SECONDS,
	LOCALIZATION_DEFAULTS_CACHE_KEY,
	LOCALIZATION_SCHEMA_VERSION,
)
from .repository import (
	LocalizationConfigurationError,
	list_active_locations,
	list_countries,
	list_enabled_currencies,
	list_enabled_languages,
)
from .serializers import country_code_to_flag
from .validators import resolve_accept_language, validate_country, validate_currency, validate_language


def get_default_preferences(*, use_cache: bool = True):
	if use_cache:
		cached = cache_get(LOCALIZATION_DEFAULTS_CACHE_KEY)
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
	cache_set(LOCALIZATION_DEFAULTS_CACHE_KEY, resolved)
	return dict(resolved), None


def resolve_guest_context(
	*,
	country: Any = None,
	currency: Any = None,
	language: Any = None,
	geo_country: str | None = None,
	accept_language: str | None = None,
):
	"""Resolve request values, trusted hints, then defaults without needless DB work."""

	resolved: dict[str, str] = {}
	sources: dict[str, str] = {}
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

	if "country" not in resolved and geo_country:
		geo_value, geo_error = validate_country(geo_country, required=False)
		if geo_value and not geo_error:
			resolved["country"] = geo_value
			sources["country"] = "geoip"
	if "language" not in resolved and accept_language:
		accepted = resolve_accept_language(accept_language)
		if accepted:
			resolved["language"] = accepted
			sources["language"] = "accept_language"

	missing = tuple(field for field in ("country", "currency", "language") if field not in resolved)
	if missing:
		defaults, error = get_default_preferences()
		if error:
			return None, error
		for field in missing:
			resolved[field] = defaults[field]
			sources[field] = "default"
	return {**resolved, "sources": sources}, None


def _serialize_country_row(row: Any) -> dict[str, Any]:
	code = (row.code or "").upper() or None
	return {"id": row.name, "name": row.name, "code": code, "flag": country_code_to_flag(code)}


def get_locale_bundle_payload(*, use_cache: bool = True):
	if use_cache:
		cached = cache_get(LOCALIZATION_BUNDLE_CACHE_KEY)
		if cached:
			return cached, None
	defaults, error = get_default_preferences(use_cache=use_cache)
	if error:
		return None, error
	try:
		countries = [_serialize_country_row(row) for row in list_countries()]
		currencies = [
			{
				"id": row.name,
				"code": row.name,
				"symbol": row.get("symbol") or None,
				"name": row.get("currency_name") or row.name,
			}
			for row in list_enabled_currencies()
		]
		languages = [
			{
				"id": row.name,
				"code": (row.language_code or row.name or "").lower(),
				"name": row.language_name or row.name,
				"flag": row.get("flag") or None,
			}
			for row in list_enabled_languages()
		]
	except LocalizationConfigurationError:
		return None, fail("Localization master data is misconfigured.", error="CONFIG_ERROR")

	if defaults["country"] not in {row["id"] for row in countries}:
		return None, fail("System default country is missing from master data.", error="CONFIG_ERROR")
	if defaults["currency"] not in {row["id"] for row in currencies}:
		return None, fail("System default currency is disabled or missing.", error="CONFIG_ERROR")
	if defaults["language"] not in {row["id"] for row in languages}:
		return None, fail("System default language is disabled or missing.", error="CONFIG_ERROR")

	payload = {
		"schema_version": LOCALIZATION_SCHEMA_VERSION,
		"cache_ttl_seconds": LOCALIZATION_CACHE_TTL_SECONDS,
		"countries": countries,
		"currencies": currencies,
		"languages": languages,
		"defaults": dict(defaults),
	}
	cache_set(LOCALIZATION_BUNDLE_CACHE_KEY, payload)
	return copy.deepcopy(payload), None


def get_locations_page(*, country: str, search: str, limit: int, offset: int) -> dict[str, Any]:
	rows = list_active_locations(country=country, search=search, limit=limit, offset=offset)
	has_more = len(rows) > limit
	locations = [
		{
			"id": row.name,
			"name": row.location,
			"country": row.country,
		}
		for row in rows[:limit]
	]
	return {
		"schema_version": LOCALIZATION_SCHEMA_VERSION,
		"country": country,
		"locations": locations,
		"pagination": {
			"limit": limit,
			"offset": offset,
			"returned": len(locations),
			"has_more": has_more,
			"next_offset": offset + limit if has_more else None,
		},
	}
