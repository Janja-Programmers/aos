"""Shared account-status helpers.

AOS uses recoverable soft deletion:
- User.enabled blocks login.
- AOS Profile.account_status / is_deleted stores app-level account state.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from .responses import fail


ACCOUNT_STATUS_ACTIVE = "Active"
ACCOUNT_STATUS_DELETED = "Deleted"
ACCOUNT_STATUS_SUSPENDED = "Suspended"

DELETED_ACCOUNT_MESSAGE = "This account has been deleted. Please restore it to continue."


def _profile_has_field(fieldname: str) -> bool:
    """Return True when AOS Profile has a field.

    Defensive helper so deploys are less brittle while migrations are running.
    """
    try:
        return bool(frappe.get_meta("AOS Profile").has_field(fieldname))
    except Exception:
        return False


def _select_existing_profile_fields(fieldnames: list[str]) -> list[str]:
    return [fieldname for fieldname in fieldnames if _profile_has_field(fieldname)]


def get_account_state(user: str) -> dict[str, Any]:
    """Return soft-delete/account-status state for a user.

    Missing status fields are treated as an active legacy account.
    """
    user = (user or "").strip()

    if not user:
        return {
            "exists": False,
            "account_status": ACCOUNT_STATUS_ACTIVE,
            "is_deleted": False,
            "can_restore": False,
            "restore_deadline": None,
        }

    if not frappe.db.exists("AOS Profile", user):
        return {
            "exists": False,
            "account_status": ACCOUNT_STATUS_ACTIVE,
            "is_deleted": False,
            "can_restore": False,
            "restore_deadline": None,
        }

    fields = _select_existing_profile_fields(
        [
            "account_status",
            "is_deleted",
            "restore_deadline",
            "deleted_at",
            "restored_at",
        ]
    )

    if not fields:
        return {
            "exists": True,
            "account_status": ACCOUNT_STATUS_ACTIVE,
            "is_deleted": False,
            "can_restore": False,
            "restore_deadline": None,
        }

    profile = frappe.db.get_value(
        "AOS Profile",
        user,
        fields,
        as_dict=True,
    ) or {}

    status = profile.get("account_status") or ACCOUNT_STATUS_ACTIVE
    is_deleted = bool(int(profile.get("is_deleted") or 0)) or status == ACCOUNT_STATUS_DELETED
    restore_deadline = profile.get("restore_deadline")

    can_restore = bool(is_deleted)
    if can_restore and restore_deadline:
        can_restore = now_datetime() <= restore_deadline

    return {
        "exists": True,
        "account_status": status,
        "is_deleted": is_deleted,
        "can_restore": can_restore,
        "restore_deadline": restore_deadline,
        "deleted_at": profile.get("deleted_at"),
        "restored_at": profile.get("restored_at"),
    }


def is_account_deleted(user: str) -> bool:
    return bool(get_account_state(user).get("is_deleted"))


def can_restore_account(user: str) -> bool:
    return bool(get_account_state(user).get("can_restore"))


def deleted_account_response(*, restorable: bool | None = None):
    """Standard response for deleted-account blocks."""
    can_restore = bool(restorable) if restorable is not None else False

    return fail(
        DELETED_ACCOUNT_MESSAGE,
        code="ACCOUNT_DELETED_RESTORABLE" if can_restore else "ACCOUNT_DELETED",
        data={"can_restore": can_restore},
        http_status=403,
    )


def ensure_account_active(user: str):
    """Return fail response if account is deleted/blocked, else None."""
    state = get_account_state(user)

    if state.get("is_deleted"):
        return deleted_account_response(restorable=bool(state.get("can_restore")))

    return None
