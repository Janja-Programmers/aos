"""Shared account-state checks used by Auth and feature authorization."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.accounts.constants import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DELETED,
    ACCOUNT_STATUS_SUSPENDED,
    PURGE_STATUS_COMPLETED,
    PURGE_STATUS_PURGING,
)

from .responses import fail

DELETED_ACCOUNT_MESSAGE = "This account has been deleted. Please restore it to continue."


def get_account_state(user: str) -> dict[str, Any]:
    user = str(user or "").strip()
    base = {
        "exists": False,
        "enabled": False,
        "account_status": ACCOUNT_STATUS_ACTIVE,
        "is_deleted": False,
        "is_suspended": False,
        "can_restore": False,
        "restore_deadline": None,
        "purge_status": None,
    }
    if not user:
        return base
    profile = frappe.db.get_value(
        "AOS Profile",
        {"user": user},
        [
            "account_status", "restore_deadline", "deleted_at", "restored_at",
            "purge_status", "purge_started_at", "purge_completed_at",
        ],
        as_dict=True,
    )
    if not profile:
        return base
    status = profile.get("account_status") or ACCOUNT_STATUS_ACTIVE
    deleted = status == ACCOUNT_STATUS_DELETED
    deadline = profile.get("restore_deadline")
    purge_status = profile.get("purge_status") or None
    purge_locked = purge_status in {PURGE_STATUS_PURGING, PURGE_STATUS_COMPLETED}
    can_restore = deleted and not purge_locked and (not deadline or now_datetime() <= deadline)
    enabled = bool(int(frappe.db.get_value("User", user, "enabled") or 0))
    return {
        "exists": True,
        "enabled": enabled,
        "account_status": status,
        "is_deleted": deleted,
        "is_suspended": status == ACCOUNT_STATUS_SUSPENDED,
        "can_restore": bool(can_restore),
        "restore_deadline": deadline,
        "purge_status": purge_status,
        "deleted_at": profile.get("deleted_at"),
        "restored_at": profile.get("restored_at"),
        "purge_started_at": profile.get("purge_started_at"),
        "purge_completed_at": profile.get("purge_completed_at"),
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


def ensure_account_active(user: str, *, state: dict[str, Any] | None = None):
    state = state or get_account_state(user)
    if state.get("is_deleted"):
        return deleted_account_response(restorable=bool(state.get("can_restore")))
    if state.get("is_suspended"):
        return fail("Account suspended.", error="ACCOUNT_SUSPENDED", http_status=403)
    return None
