"""Shared account-state checks used by Auth and feature authorization."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.accounts.constants import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DEACTIVATED,
    ACCOUNT_STATUS_DELETED,
    ACCOUNT_STATUS_SUSPENDED,
)

from .responses import fail

DELETED_ACCOUNT_MESSAGE = "This account has been deleted. Please restore it to continue."


def _profile_has_field(fieldname: str) -> bool:
    try:
        return bool(frappe.get_meta("AOS Profile").has_field(fieldname))
    except Exception:
        return False


def get_account_state(user: str) -> dict[str, Any]:
    user = str(user or "").strip()
    base = {
        "exists": False,
        "account_status": ACCOUNT_STATUS_ACTIVE,
        "is_deleted": False,
        "is_deactivated": False,
        "is_suspended": False,
        "can_restore": False,
        "restore_deadline": None,
    }
    if not user or not frappe.db.exists("AOS Profile", user):
        return base
    wanted = ["account_status", "is_deleted", "restore_deadline", "deleted_at", "deactivated_at", "restored_at"]
    fields = [field for field in wanted if _profile_has_field(field)]
    profile = frappe.db.get_value("AOS Profile", user, fields, as_dict=True) or {}
    status = profile.get("account_status") or ACCOUNT_STATUS_ACTIVE
    deleted = bool(int(profile.get("is_deleted") or 0)) or status == ACCOUNT_STATUS_DELETED
    deadline = profile.get("restore_deadline")
    can_restore = deleted and (not deadline or now_datetime() <= deadline)
    return {
        "exists": True,
        "account_status": status,
        "is_deleted": deleted,
        "is_deactivated": status == ACCOUNT_STATUS_DEACTIVATED,
        "is_suspended": status == ACCOUNT_STATUS_SUSPENDED,
        "can_restore": bool(can_restore),
        "restore_deadline": deadline,
        "deleted_at": profile.get("deleted_at"),
        "deactivated_at": profile.get("deactivated_at"),
        "restored_at": profile.get("restored_at"),
    }


def is_account_deleted(user: str) -> bool:
    return bool(get_account_state(user).get("is_deleted"))


def can_restore_account(user: str) -> bool:
    return bool(get_account_state(user).get("can_restore"))


def deleted_account_response(*, restorable: bool | None = None):
    can_restore = bool(restorable)
    return fail(
        DELETED_ACCOUNT_MESSAGE,
        error="ACCOUNT_DELETED_RESTORABLE" if can_restore else "ACCOUNT_DELETED",
        data={"can_restore": can_restore},
        http_status=403,
    )


def ensure_account_active(user: str):
    state = get_account_state(user)
    if state.get("is_deleted"):
        return deleted_account_response(restorable=bool(state.get("can_restore")))
    if state.get("is_deactivated"):
        return fail("Account deactivated.", error="ACCOUNT_DEACTIVATED", http_status=403)
    if state.get("is_suspended"):
        return fail("Account suspended.", error="ACCOUNT_SUSPENDED", http_status=403)
    return None
