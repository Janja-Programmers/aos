"""Stable, minimal public serialization for localization data."""

from __future__ import annotations

from typing import Any


def _value(source: Any, fieldname: str) -> Any:
	if isinstance(source, dict):
		return source.get(fieldname)
	return getattr(source, fieldname, None)


def country_code_to_flag(code: str | None) -> str | None:
	code = str(code or "").strip().upper()
	if len(code) != 2 or not code.isalpha():
		return None
	return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


def serialize_preference(preference: Any) -> dict[str, Any]:
	"""Serialize stored preference IDs without master-data lookups."""

	location = str(_value(preference, "location") or "").strip() or None
	return {
		"country": str(_value(preference, "country") or "") or None,
		"currency": str(_value(preference, "currency") or "") or None,
		"language": str(_value(preference, "language") or "") or None,
		"location": location,
	}


def serialize_context(context: dict[str, Any]) -> dict[str, Any]:
	"""Serialize the hot-path resolver minimally; clients map IDs via the bundle."""

	from .constants import LOCALIZATION_SCHEMA_VERSION

	return {
		"schema_version": LOCALIZATION_SCHEMA_VERSION,
		"country": context["country"],
		"currency": context["currency"],
		"language": context["language"],
		"sources": dict(context["sources"]),
	}
