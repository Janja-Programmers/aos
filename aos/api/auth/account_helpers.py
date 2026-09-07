"""Authentication account bootstrap and safe observability helpers."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.locale_hints import accept_language_hint, geo_country_hint
from aos.api.shared.responses import fail
from aos.services.accounts.identity import get_profile_for_user, profile_name_for_user
from aos.services.accounts.observability import account_log
from aos.services.localization import resolve_guest_context
from aos.services.user_preference_service import clear_user_preference_cache, get_user_preference
from aos.utils.privacy import opaque_identifier


def create_aos_profile(user: str):
    """Create the required AOS Profile for a newly-created User.

    Frappe User owns authentication identity only. AOS Profile owns product
    profile data and receives its own immutable ACC-* primary key.
    """
    if not user:
        return None
    existing = get_profile_for_user(user)
    if existing:
        return existing
    display_name = frappe.db.get_value("User", user, "full_name") or "AOS User"
    profile = frappe.new_doc("AOS Profile")
    profile.user = user
    profile.display_name = display_name
    profile.account_status = "Active"
    profile.insert(ignore_permissions=True)
    account_log("account.bootstrap.completed", user=user)
    return profile



def profile_display_name(user: str) -> str:
    """Return the canonical AOS display name for an existing account."""
    value = frappe.db.get_value("AOS Profile", {"user": str(user or "").strip()}, "display_name")
    return str(value or "").strip()

def create_user_preference(
    user: str,
    *,
    country: str | None = None,
    currency: str | None = None,
    language: str | None = None,
):
    """Create Localization-owned defaults for a newly-created account."""
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
        name = frappe.db.get_value("AOS User Preference", {"user": user}, "name")
        if name:
            return frappe.get_doc("AOS User Preference", name), None
        return None, fail("Failed to initialize user preference.", error="PREFERENCE_CREATE_FAILED")
    except Exception as exc:
        account_log("account.bootstrap.failed", user=user, outcome="failure", failure_category="preference")
        from .observability import log_auth_exception

        log_auth_exception("AOS Preference Bootstrap Failed", exc, operation="preference_bootstrap")
        return None, fail("Failed to initialize user preference.", error="PREFERENCE_CREATE_FAILED")


def create_auth_bootstrap(
    user: str,
    *,
    country: str | None = None,
    currency: str | None = None,
    language: str | None = None,
):
    create_aos_profile(user)
    return create_user_preference(user, country=country, currency=currency, language=language)


def assert_auth_bootstrap(user: str, *, profile_exists: bool | None = None):
    """Validate required identity rows without mutating a high-frequency read.

    Missing bootstrap state is an internal account invariant, not a client
    authorization distinction. Keep the public error stable and log only a
    hashed user identifier for operators.
    """
    if profile_exists is None:
        profile_exists = bool(profile_name_for_user(user))
    if not profile_exists:
        safe_log_auth_event("AOS Auth Bootstrap Missing", user=user, reason="profile")
        return fail("Account bootstrap temporarily unavailable.", error="ACCOUNT_BOOTSTRAP_UNAVAILABLE")
    if not frappe.db.exists("AOS User Preference", {"user": user}):
        safe_log_auth_event("AOS Auth Bootstrap Missing", user=user, reason="preference")
        return fail("Account bootstrap temporarily unavailable.", error="ACCOUNT_BOOTSTRAP_UNAVAILABLE")
    return None


def user_for_email(email: str) -> str | None:
    email = str(email or "").strip().lower()
    if not email:
        return None
    # AOS uses the canonical normalized email as the Frappe User name. Primary
    # key lookup is cheaper and avoids supporting a second email-alias identity
    # path that could drift from the Authentication contract.
    return email if frappe.db.exists("User", email) else None


def safe_log_auth_event(
    title: str,
    *,
    identifier: str | None = None,
    user: str | None = None,
    reason: str | None = None,
) -> None:
    """Log only site-keyed opaque identifiers; never credentials/session/token values."""
    payload: dict[str, Any] = {"reason": reason or "unspecified"}
    if identifier:
        payload["identifier_hash"] = opaque_identifier(identifier)
    if user:
        payload["user_hash"] = opaque_identifier(user)
    try:
        frappe.logger("aos.auth").warning("%s %s", title, payload)
    except Exception:
        frappe.log_error(message=str(payload), title=title[:140])
