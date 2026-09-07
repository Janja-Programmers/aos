"""Guards for Frappe whitelisted auth methods outside the AOS public contract.

AOS Website Users must not bypass Authentication rate limits, bootstrap
invariants, recovery semantics, or session response contracts through Frappe's
generic website methods. Frappe System Users keep their normal Desk/admin
password flows, including reset links.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.core.doctype.user.user import reset_password as _frappe_reset_password
from frappe.core.doctype.user.user import update_password as _frappe_update_password
from frappe.core.doctype.user.user import verify_password as _frappe_verify_password
from frappe.utils import sha256_hash


def block_frappe_signup(*args, **kwargs):
    """Disable Frappe's parallel Website User registration path."""
    return 0, _("We could not create an account with the provided details.")


def _generic_recovery_response() -> None:
    frappe.msgprint(
        msg=_(
            "If this email is registered with us, recovery instructions are available "
            "through the appropriate account recovery flow."
        ),
        title=_("Password Recovery"),
    )


def guard_frappe_reset_password(user: str, *args, **kwargs):
    """Allow Frappe recovery for System Users; keep AOS Website Users on AOS v1."""
    user_type = frappe.db.get_value("User", user, "user_type") if isinstance(user, str) and user else None
    if user_type and user_type != "Website User":
        return _frappe_reset_password(user, *args, **kwargs)
    _generic_recovery_response()
    return None


def _session_user_type() -> str | None:
    user = str(getattr(frappe.session, "user", "Guest") or "Guest")
    if user == "Guest":
        return None
    return frappe.get_cached_value("User", user, "user_type")


def _system_user_for_reset_key(key: str | None) -> bool:
    """Return True only when a reset key currently belongs to a System User."""
    if not isinstance(key, str) or not key:
        return False
    target = frappe.db.get_value(
        "User",
        {"reset_password_key": sha256_hash(key)},
        ["name", "user_type"],
        as_dict=True,
    )
    return bool(target and target.get("user_type") != "Website User")


def _require_system_user_session() -> None:
    if _session_user_type() != "System User":
        raise frappe.PermissionError(_("Use the AOS Authentication API."))


def guard_frappe_update_password(*args, **kwargs):
    """Preserve System User reset links/current-password flow; block Website Users."""
    key = kwargs.get("key")
    if key is None and len(args) >= 3:
        key = args[2]
    if _system_user_for_reset_key(key) or _session_user_type() == "System User":
        return _frappe_update_password(*args, **kwargs)
    raise frappe.PermissionError(_("Use the AOS Authentication API."))


def guard_frappe_verify_password(*args, **kwargs):
    _require_system_user_session()
    return _frappe_verify_password(*args, **kwargs)
