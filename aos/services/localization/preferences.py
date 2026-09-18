"""Canonical persisted localization preferences for authenticated accounts.

Localization owns validation, storage semantics, cache invalidation, and the
country/location consistency invariant. Accounts exposes this service through
its authenticated preference endpoints but does not duplicate these rules.
"""

from __future__ import annotations

import hashlib
from typing import Any

import frappe

from aos.api.shared.responses import fail

from .validators import validate_country, validate_currency, validate_language, validate_location

USER_PREFERENCE_CACHE_SCHEMA = "v3"
USER_PREFERENCE_CACHE_TTL_SECONDS = 300
PREFERENCE_FIELDS = ("country", "currency", "language", "location")
PREFERENCE_DB_FIELDS = ("name", "user", *PREFERENCE_FIELDS)
_UNSET = object()


def _cache_key(user: str) -> str:
    digest = hashlib.sha256(str(user or "").encode("utf-8")).hexdigest()
    return f"aos:user_pref:v4:{digest}"


def _delete_user_preference_cache(user: str) -> None:
    try:
        frappe.cache().delete_value(_cache_key(user))
    except Exception:
        return


def clear_user_preference_cache(user: str | None) -> None:
    """Invalidate now and again after commit to prevent stale cross-node refill."""

    user = str(user or "").strip()
    if not user:
        return
    _delete_user_preference_cache(user)
    manager = getattr(frappe.db, "after_commit", None)
    if manager is None or not hasattr(manager, "add"):
        return
    manager.add(lambda: _delete_user_preference_cache(user))


def get_user_preference(user: str, *, use_cache: bool = True):
    """Return persisted preference without creating or repairing state."""

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
        list(PREFERENCE_DB_FIELDS),
        as_dict=True,
    )
    if not preference:
        return None

    payload = {field: preference.get(field) for field in PREFERENCE_DB_FIELDS}
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
    """Lock and project exactly one preference row for mutation."""

    user = str(user or "").strip()
    if not user or user == "Guest":
        return None
    select_fields = ", ".join(PREFERENCE_DB_FIELDS)
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
    """Validate an optional active location through Localization's canonical rule."""

    location, error = validate_location(value, country=country, required=False)
    if error:
        return None, error
    return location or "", None


def update_user_preference(
    user: str,
    *,
    country: Any = _UNSET,
    currency: Any = _UNSET,
    language: Any = _UNSET,
    location: Any = _UNSET,
):
    """Apply a strict partial update under one row lock without committing.

    Country is a mutable browsing/buyer market. When it changes, an existing
    selected location that belongs to another country is cleared. Currency and
    language remain independent unless explicitly supplied by the client.
    """

    supplied = {
        key: value
        for key, value in {
            "country": country,
            "currency": currency,
            "language": language,
            "location": location,
        }.items()
        if value is not _UNSET
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
    elif next_location and next_country != pref.country:
        _validated_location, error = validate_location_preference(next_location, country=next_country)
        if error:
            next_location = ""

    changed = any(
        (
            str(next_country or "") != str(pref.country or ""),
            str(next_currency or "") != str(pref.currency or ""),
            str(next_language or "") != str(pref.language or ""),
            str(next_location or "") != str(pref.get("location") or ""),
        )
    )
    if not changed:
        return frappe._dict({field: pref.get(field) for field in PREFERENCE_DB_FIELDS}), None

    doc = frappe.get_doc("AOS User Preference", pref.name)
    doc.country = next_country
    doc.currency = next_currency
    doc.language = next_language
    doc.location = next_location
    doc.save(ignore_permissions=True)
    return doc, None
