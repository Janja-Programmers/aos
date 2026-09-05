"""Idempotent Accounts bootstrap helpers owned by authentication flows."""

from __future__ import annotations

import hashlib
from typing import Any

import frappe

from aos.api.shared.locale_hints import accept_language_hint, geo_country_hint
from aos.api.shared.responses import fail
from aos.services.accounts.identity import ensure_public_account_id
from aos.services.accounts.observability import account_log
from aos.services.localization import resolve_guest_context
from aos.services.user_preference_service import clear_user_preference_cache, get_user_preference


def _clear_preference_cache(user: str) -> None:
    clear_user_preference_cache(user)


def ensure_aos_profile(user: str):
    if not user:
        return None
    if frappe.db.exists("AOS Profile", user):
        profile = frappe.get_doc("AOS Profile", user)
        changed = False
        if not getattr(profile, "display_name", None):
            profile.display_name = str(frappe.db.get_value("User", user, "full_name") or "AOS User")
            changed = True
        if not getattr(profile, "public_id", None):
            ensure_public_account_id(profile)
        if changed:
            profile.save(ignore_permissions=True)
        return profile
    user_row = frappe.db.get_value("User", user, ["full_name", "bio", "mobile_no", "birth_date", "gender", "location"], as_dict=True) or {}
    profile = frappe.new_doc("AOS Profile")
    profile.user = user
    profile.display_name = user_row.get("full_name") or "AOS User"
    profile.bio = user_row.get("bio") or ""
    profile.phone = user_row.get("mobile_no") or ""
    profile.date_of_birth = user_row.get("birth_date")
    profile.gender = user_row.get("gender") or ""
    profile.location = user_row.get("location") or ""
    profile.account_status = "Active"
    profile.is_deleted = 0
    profile.insert(ignore_permissions=True)
    account_log("account.bootstrap.completed", user=user)
    return profile


def ensure_user_preference(user: str, *, country: str | None = None, currency: str | None = None, language: str | None = None):
    if not user:
        return None, fail("User is required.", error="VALIDATION_ERROR")
    existing = get_user_preference(user)
    if existing:
        return frappe.get_doc("AOS User Preference", existing.name), None
    resolved, error = resolve_guest_context(
        country=country,
        currency=currency,
        language=language,
        geo_country=geo_country_hint(),
        accept_language=accept_language_hint(),
    )
    if error:
        return None, error
    try:
        pref = frappe.new_doc("AOS User Preference")
        pref.user = user
        pref.country = resolved["country"]
        pref.currency = resolved["currency"]
        pref.language = resolved["language"]
        pref.insert(ignore_permissions=True)
        clear_user_preference_cache(user)
        return pref, None
    except frappe.DuplicateEntryError:
        pref_name = frappe.db.get_value("AOS User Preference", {"user": user}, "name")
        if pref_name:
            return frappe.get_doc("AOS User Preference", pref_name), None
        return None, fail("Failed to initialize user preference.", error="PREFERENCE_CREATE_FAILED")
    except Exception:
        account_log("account.bootstrap.failed", user=user, outcome="failure", failure_category="preference")
        frappe.log_error(frappe.get_traceback(), "AOS Preference Bootstrap Failed")
        return None, fail("Failed to initialize user preference.", error="PREFERENCE_CREATE_FAILED")


def ensure_auth_bootstrap(user: str, *, country: str | None = None, currency: str | None = None, language: str | None = None):
    ensure_aos_profile(user)
    return ensure_user_preference(user, country=country, currency=currency, language=language)


def safe_user_doc(user: str):
    return frappe.get_doc("User", user) if user else None


def user_exists_by_identifier(identifier: str) -> str | None:
    identifier = str(identifier or "").strip().lower()
    if not identifier:
        return None
    return frappe.db.get_value("User", {"email": identifier}, "name") or (identifier if frappe.db.exists("User", identifier) else None)


def safe_log_auth_event(title: str, *, identifier: str | None = None, user: str | None = None, reason: str | None = None) -> None:
    payload: dict[str, Any] = {"reason": reason or "unspecified"}
    if identifier:
        payload["identifier_hash"] = hashlib.sha256(identifier.encode("utf-8")).hexdigest()[:16]
    if user:
        payload["user_hash"] = hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]
    try:
        frappe.logger("aos.auth").warning("%s %s", title, payload)
    except Exception:
        frappe.log_error(message=str(payload), title=title[:140])
