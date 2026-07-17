"""Canonical localization master-data and preference service.

This module is request-agnostic so DocTypes, auth, accounts, and API handlers
can share the same validation and serialization contract without depending on
localization endpoint modules.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.responses import fail
from aos.utils.aos_settings import get_aos_settings_snapshot


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def country_code_to_flag(code: str | None) -> str | None:
    code = _clean(code).upper()
    if len(code) != 2 or not code.isalpha():
        return None
    return "".join(chr(0x1F1E6 + ord(char) - ord("A")) for char in code)


def _meta_has_field(doctype: str, fieldname: str) -> bool:
    return bool(frappe.get_meta(doctype).has_field(fieldname))


def validate_country(value: Any, *, required: bool = True):
    value = _clean(value)
    if not value:
        return (None, fail("Country is required.", error="INVALID_COUNTRY")) if required else (None, None)
    name = value if frappe.db.exists("Country", value) else frappe.db.get_value("Country", {"code": value.upper()}, "name")
    if not name:
        return None, fail("Invalid country.", error="INVALID_COUNTRY", data={"field": "country"})
    return name, None


def validate_currency(value: Any, *, required: bool = True):
    value = _clean(value).upper()
    if not value:
        return (None, fail("Currency is required.", error="INVALID_CURRENCY")) if required else (None, None)
    if not frappe.db.exists("Currency", value):
        return None, fail("Invalid currency.", error="INVALID_CURRENCY", data={"field": "currency"})
    if _meta_has_field("Currency", "enabled") and not int(frappe.db.get_value("Currency", value, "enabled") or 0):
        return None, fail("Currency is disabled.", error="DISABLED_CURRENCY", data={"field": "currency"})
    return value, None


def validate_language(value: Any, *, required: bool = True):
    value = _clean(value)
    if not value:
        return (None, fail("Language is required.", error="INVALID_LANGUAGE")) if required else (None, None)
    fields = ["name", "language_name", "language_code"]
    row = frappe.db.get_value("Language", value, fields, as_dict=True)
    if not row:
        row = frappe.db.get_value("Language", {"language_name": value}, fields, as_dict=True)
    if not row:
        row = frappe.db.get_value("Language", {"language_code": value.lower()}, fields, as_dict=True)
    if not row:
        return None, fail("Invalid language.", error="INVALID_LANGUAGE", data={"field": "language"})
    if _meta_has_field("Language", "enabled") and not int(frappe.db.get_value("Language", row.name, "enabled") or 0):
        return None, fail("Language is disabled.", error="DISABLED_LANGUAGE", data={"field": "language"})
    return row.name, None


def accept_language_candidates(header: str | None) -> list[str]:
    """Return quality-ordered exact and primary language tags."""
    parsed: list[tuple[float, int, str]] = []
    for position, item in enumerate(str(header or "").split(",")):
        parts = [part.strip() for part in item.split(";") if part.strip()]
        tag = parts[0] if parts else ""
        if not tag or tag == "*":
            continue
        quality = 1.0
        for parameter in parts[1:]:
            if parameter.lower().startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        if quality > 0:
            parsed.append((quality, position, tag))
    result: list[str] = []
    for _quality, _position, tag in sorted(parsed, key=lambda row: (-row[0], row[1])):
        for candidate in (tag, tag.split("-", 1)[0]):
            if candidate and candidate not in result:
                result.append(candidate)
    return result


def resolve_accept_language(header: str | None) -> str | None:
    for candidate in accept_language_candidates(header):
        language, error = validate_language(candidate, required=False)
        if language and not error:
            return language
    return None


def serialize_country(value: str) -> dict[str, Any]:
    row = frappe.db.get_value("Country", value, ["name", "code"], as_dict=True) or {}
    name = row.get("name") or value
    code = (row.get("code") or "").upper() or None
    return {"id": name, "name": name, "code": code, "flag": country_code_to_flag(code)}


def serialize_currency(value: str, *, include_state: bool = False, default: str | None = None) -> dict[str, Any]:
    fields = ["name", "symbol"] + (["currency_name"] if _meta_has_field("Currency", "currency_name") else [])
    row = frappe.db.get_value("Currency", value, fields, as_dict=True) or {}
    result = {"id": value, "code": value, "symbol": row.get("symbol") or None, "name": row.get("currency_name") or value}
    if include_state:
        result.update({"enabled": True, "is_default": value == default})
    return result


def serialize_language(value: str, *, include_state: bool = False, default: str | None = None) -> dict[str, Any]:
    fields = ["name", "language_name", "language_code"]
    if _meta_has_field("Language", "flag"):
        fields.append("flag")
    row = frappe.db.get_value("Language", value, fields, as_dict=True) or {}
    result = {"id": row.get("name") or value, "code": (row.get("language_code") or row.get("name") or "").lower(), "name": row.get("language_name") or value, "flag": row.get("flag") or None}
    if include_state:
        result.update({"enabled": True, "is_default": (row.get("name") or value) == default})
    return result


def get_default_preferences():
    settings = get_aos_settings_snapshot()
    values = {"country": settings.default_country, "currency": settings.default_currency, "language": settings.default_language}
    validators = {"country": validate_country, "currency": validate_currency, "language": validate_language}
    resolved: dict[str, str] = {}
    for field, validator in validators.items():
        resolved[field], error = validator(values[field])
        if error:
            return None, fail(f"System default {field} is missing or invalid.", error="CONFIG_ERROR", data={"field": field})
    return resolved, None


def get_all_countries() -> list[dict[str, Any]]:
    return [serialize_country(row.name) for row in frappe.get_all("Country", fields=["name"], order_by="name asc", limit_page_length=0)]


def get_enabled_currencies(default: str | None = None) -> list[dict[str, Any]]:
    filters = {"enabled": 1} if _meta_has_field("Currency", "enabled") else {}
    return [serialize_currency(row.name, include_state=True, default=default) for row in frappe.get_all("Currency", filters=filters, fields=["name"], order_by="name asc", limit_page_length=0)]


def get_enabled_languages(default: str | None = None) -> list[dict[str, Any]]:
    filters = {"enabled": 1} if _meta_has_field("Language", "enabled") else {}
    return [serialize_language(row.name, include_state=True, default=default) for row in frappe.get_all("Language", filters=filters, fields=["name"], order_by="language_name asc", limit_page_length=0)]


def serialize_preference(preference: Any, *, is_country_locked: bool = False) -> dict[str, Any]:
    return {"country": serialize_country(preference.country), "currency": serialize_currency(preference.currency), "language": serialize_language(preference.language), "is_country_locked": bool(is_country_locked)}


def serialize_context(context: dict[str, Any]) -> dict[str, Any]:
    return {"country": serialize_country(context["country"]), "currency": serialize_currency(context["currency"]), "language": serialize_language(context["language"]), "sources": context["sources"]}


def is_country_locked(user: str) -> bool:
    seller = frappe.db.get_value("AOS Seller", {"user": user}, "name")
    return bool(seller and frappe.db.exists("AOS Ad", {"seller": seller}))
