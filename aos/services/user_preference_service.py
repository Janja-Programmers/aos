"""Canonical persistence, locking, validation, and cache for account preferences."""

from __future__ import annotations

import hashlib
from typing import Any

import frappe

from aos.api.shared.responses import fail
from aos.services.localization_service import validate_country, validate_currency, validate_language

USER_PREFERENCE_CACHE_SCHEMA = "v2"
USER_PREFERENCE_CACHE_TTL_SECONDS = 300
PREFERENCE_FIELDS = ("country", "currency", "language", "location")


def _cache_key(user: str) -> str:
    digest = hashlib.sha256(str(user or "").encode("utf-8")).hexdigest()
    return f"aos:user_pref:v3:{digest}"


def clear_user_preference_cache(user: str | None) -> None:
    user = str(user or "").strip()
    if not user:
        return
    try:
        frappe.cache().delete_value(_cache_key(user))
    except Exception:
        pass


def clear_user_preference_cache_for_doc(doc: Any, _method: str | None = None) -> None:
    clear_user_preference_cache(getattr(doc, "user", None))


def _preference_fields() -> list[str]:
    fields = ["name", "user", "country", "currency", "language"]
    try:
        if frappe.get_meta("AOS User Preference").has_field("location"):
            fields.append("location")
    except Exception:
        pass
    return fields


def get_user_preference(user: str, *, use_cache: bool = True):
    user = str(user or "").strip()
    if not user or user == "Guest":
        return None
    cache = None
    try:
        cache = frappe.cache()
    except Exception:
        pass
    key = _cache_key(user)
    if use_cache and cache is not None:
        try:
            cached = cache.get_value(key)
            if isinstance(cached, dict) and cached.get("_schema") == USER_PREFERENCE_CACHE_SCHEMA:
                payload = dict(cached)
                payload.pop("_schema", None)
                return frappe._dict(payload)
        except Exception:
            pass
    preference = frappe.db.get_value(
        "AOS User Preference",
        {"user": user},
        _preference_fields(),
        as_dict=True,
    )
    if not preference:
        return None
    payload = {field: preference.get(field) for field in _preference_fields()}
    if cache is not None:
        try:
            cache.set_value(
                key,
                {"_schema": USER_PREFERENCE_CACHE_SCHEMA, **payload},
                expires_in_sec=USER_PREFERENCE_CACHE_TTL_SECONDS,
            )
        except Exception:
            pass
    return frappe._dict(payload)


def get_user_preference_for_update(user: str):
    user = str(user or "").strip()
    if not user or user == "Guest":
        return None
    select_fields = ", ".join(_preference_fields())
    rows = frappe.db.sql(
        f"""
        SELECT {select_fields}
        FROM `tabAOS User Preference`
        WHERE user = %s
        LIMIT 1
        FOR UPDATE
        """,
        (user,),
        as_dict=True,
    )
    return rows[0] if rows else None


def validate_location_preference(value: Any, *, country: str | None = None):
    location = str(value or "").strip()
    if not location:
        return "", None
    row = frappe.db.get_value(
        "AOS Location",
        location,
        ["name", "country", "is_active"],
        as_dict=True,
    )
    if not row or not int(row.is_active or 0):
        return None, fail("Invalid or disabled location.", error="INVALID_LOCATION")
    if country and row.country != country:
        return None, fail("Location does not belong to the selected country.", error="INVALID_LOCATION")
    return row.name, None


def is_country_locked(user: str) -> bool:
    user = str(user or "").strip()
    if not user or user == "Guest":
        return False
    seller = frappe.db.get_value("AOS Seller", {"user": user}, "name")
    if not seller:
        return False
    # Seller ownership itself locks the market because seller tax, catalog, and
    # operational policies are market-bound. Existing ad activity remains an
    # additional defensive signal for partially migrated sellers.
    return True


def update_user_preference(
    user: str,
    *,
    country: Any = None,
    currency: Any = None,
    language: Any = None,
    location: Any = None,
):
    """Apply a strict partial update under one row lock without committing."""
    supplied = {
        key: value
        for key, value in {
            "country": country,
            "currency": currency,
            "language": language,
            "location": location,
        }.items()
        if value is not None
    }
    if not supplied:
        return None, fail("At least one preference field is required.", error="VALIDATION_ERROR")

    pref = get_user_preference_for_update(user)
    if not pref:
        return None, fail("Preference data is missing.", error="PREFERENCE_MISSING")

    next_country = pref.country
    next_currency = pref.currency
    next_language = pref.language
    next_location = pref.get("location") or ""

    if "country" in supplied:
        next_country, error = validate_country(supplied["country"])
        if error:
            return None, error
        if next_country != pref.country and is_country_locked(user):
            return None, fail("Country cannot be changed for this account.", error="COUNTRY_LOCKED")
    if "currency" in supplied:
        next_currency, error = validate_currency(supplied["currency"])
        if error:
            return None, error
    if "language" in supplied:
        next_language, error = validate_language(supplied["language"])
        if error:
            return None, error
    if "location" in supplied:
        next_location, error = validate_location_preference(supplied["location"], country=next_country)
        if error:
            return None, error
    elif next_location:
        # A country change must not retain a location from another market.
        _location, error = validate_location_preference(next_location, country=next_country)
        if error:
            next_location = ""

    doc = frappe.get_doc("AOS User Preference", pref.name)
    doc.country = next_country
    doc.currency = next_currency
    doc.language = next_language
    if hasattr(doc, "location"):
        doc.location = next_location
    doc.save(ignore_permissions=True)
    clear_user_preference_cache(user)
    return doc, None
