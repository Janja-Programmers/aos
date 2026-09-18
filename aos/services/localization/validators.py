"""Canonical localization validation and request-hint normalization."""

from __future__ import annotations

import re
from typing import Any

from aos.api.shared.responses import fail

from .constants import (
	MAX_ACCEPT_LANGUAGE_ITEMS,
	MAX_ACCEPT_LANGUAGE_LENGTH,
	MAX_COUNTRY_INPUT_LENGTH,
	MAX_CURRENCY_INPUT_LENGTH,
	MAX_LANGUAGE_INPUT_LENGTH,
)
from .repository import (
	country_by_code,
	country_by_name,
	currency_by_name,
	location_by_name,
	language_by_value,
	languages_matching_candidates,
)

_LANGUAGE_TAG_RE = re.compile(r"^[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*$")


def clean_text(value: Any, *, max_length: int) -> str:
	if not isinstance(value, str):
		return ""
	text = value.strip()
	return text if len(text) <= max_length else ""


def validate_country(value: Any, *, required: bool = True):
	value = clean_text(value, max_length=MAX_COUNTRY_INPUT_LENGTH)
	if not value:
		if required:
			return None, fail("Country is required.", error="INVALID_COUNTRY", data={"field": "country"})
		return None, None
	row = country_by_name(value)
	if not row and len(value) == 2 and value.isalpha():
		row = country_by_code(value)
	if not row:
		return None, fail("Invalid country.", error="INVALID_COUNTRY", data={"field": "country"})
	return str(row.name), None


def validate_location(value: Any, *, country: str | None = None, required: bool = False):
	"""Resolve one active AOS Location and optionally enforce country ownership."""
	location = clean_text(value, max_length=140)
	if not location:
		if required:
			return None, fail("Location is required.", error="INVALID_LOCATION", data={"field": "location"})
		return None, None
	row = location_by_name(location)
	if not row:
		return None, fail("Invalid location.", error="INVALID_LOCATION", data={"field": "location"})
	if not int(row.is_active or 0):
		return None, fail("Selected location is inactive.", error="INVALID_LOCATION", data={"field": "location"})
	if country and str(row.country or "") != str(country or ""):
		return None, fail(
			"Location does not belong to the selected country.",
			error="INVALID_LOCATION",
			data={"field": "location"},
		)
	return str(row.name), None

def validate_currency(value: Any, *, required: bool = True):
	value = clean_text(value, max_length=MAX_CURRENCY_INPUT_LENGTH).upper()
	if not value:
		if required:
			return None, fail("Currency is required.", error="INVALID_CURRENCY", data={"field": "currency"})
		return None, None
	row = currency_by_name(value)
	if not row:
		return None, fail("Invalid currency.", error="INVALID_CURRENCY", data={"field": "currency"})
	if "enabled" in row and not int(row.enabled or 0):
		return None, fail("Currency is disabled.", error="DISABLED_CURRENCY", data={"field": "currency"})
	return str(row.name), None


def validate_language(value: Any, *, required: bool = True):
	value = clean_text(value, max_length=MAX_LANGUAGE_INPUT_LENGTH)
	if not value:
		if required:
			return None, fail("Language is required.", error="INVALID_LANGUAGE", data={"field": "language"})
		return None, None
	row = language_by_value(value)
	if not row:
		return None, fail("Invalid language.", error="INVALID_LANGUAGE", data={"field": "language"})
	if "enabled" in row and not int(row.enabled or 0):
		return None, fail("Language is disabled.", error="DISABLED_LANGUAGE", data={"field": "language"})
	return str(row.name), None


def canonical_language_tag(tag: str) -> str | None:
	tag = str(tag or "").strip()
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
	"""Return bounded quality-ordered exact tags followed by primary fallbacks."""

	raw_header = str(header or "")[:MAX_ACCEPT_LANGUAGE_LENGTH]
	parsed: list[tuple[float, int, str]] = []
	for position, item in enumerate(raw_header.split(",")[:MAX_ACCEPT_LANGUAGE_ITEMS]):
		parts = [part.strip() for part in item.split(";") if part.strip()]
		canonical = canonical_language_tag(parts[0] if parts else "")
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
	"""Resolve a bounded header with one database query, preserving q-order."""
	candidates = accept_language_candidates(header)
	if not candidates:
		return None
	rows = languages_matching_candidates(candidates)
	lookup: dict[str, str] = {}
	for row in rows:
		canonical = str(row.name)
		for value in (row.name, row.language_name, row.language_code):
			key = str(value or "").casefold()
			if key and key not in lookup:
				lookup[key] = canonical
	for candidate in candidates:
		resolved = lookup.get(candidate.casefold())
		if resolved:
			return resolved
	return None
