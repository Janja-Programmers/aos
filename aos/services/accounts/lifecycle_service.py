"""Locked, idempotent Accounts deletion/restore orchestration."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.session_control import revoke_account_access
from aos.services.account_deletion_service import tombstone_deleted_account_features, restore_deleted_account_features

from .constants import (
    ACCOUNT_RESTORE_WINDOW_DAYS,
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DELETED,
    PURGE_STATUS_PENDING,
    PURGE_STATUS_PURGING,
    PURGE_STATUS_COMPLETED,
)
from .errors import AccountConflictError, AccountNotFoundError
from .identity import profile_name_for_user
from .observability import account_log


class AccountLifecycleService:
    def __init__(self, media=None):
        # Durable profile media survives the recoverable deletion window.
        self.media = media

    def delete(self, *, user: str, reason: str = "") -> dict[str, Any]:
        profile = self._lock_profile(user)
        if profile.account_status == ACCOUNT_STATUS_DELETED:
            access = revoke_account_access(user)
            return {
                "status": ACCOUNT_STATUS_DELETED,
                "idempotent": True,
                "restore_deadline": profile.restore_deadline,
                **access,
            }
        account_log("account.deletion.requested", user=user)
        now = now_datetime()
        profile.account_status = ACCOUNT_STATUS_DELETED
        profile.deleted_at = now
        profile.restore_deadline = add_to_date(now, days=ACCOUNT_RESTORE_WINDOW_DAYS)
        profile.delete_reason = str(reason or "")[:300]
        profile.restored_at = None
        profile.purge_status = PURGE_STATUS_PENDING
        profile.purge_started_at = None
        profile.purge_completed_at = None
        profile.save(ignore_permissions=True)
        frappe.db.set_value("User", user, "enabled", 0, update_modified=True)
        feature_summary = tombstone_deleted_account_features(user)
        access = revoke_account_access(user)
        account_log("account.deleted", user=user)
        return {
            "status": ACCOUNT_STATUS_DELETED,
            "idempotent": False,
            "restore_deadline": profile.restore_deadline,
            "features": feature_summary,
            **access,
        }

    def restore(self, *, user: str) -> dict[str, Any]:
        profile = self._lock_profile(user)
        if profile.account_status == ACCOUNT_STATUS_ACTIVE:
            return {"status": ACCOUNT_STATUS_ACTIVE, "idempotent": True}
        if profile.account_status != ACCOUNT_STATUS_DELETED:
            raise AccountConflictError("Account is not deleted.", code="ACCOUNT_NOT_DELETED")
        purge_status = str(getattr(profile, "purge_status", "") or "")
        if purge_status in {PURGE_STATUS_PURGING, PURGE_STATUS_COMPLETED}:
            raise AccountConflictError("This account can no longer be restored.", code="RESTORE_EXPIRED")
        if profile.restore_deadline and now_datetime() > profile.restore_deadline:
            raise AccountConflictError("This account can no longer be restored.", code="RESTORE_EXPIRED")
        profile.account_status = ACCOUNT_STATUS_ACTIVE
        profile.deleted_at = None
        profile.delete_reason = ""
        profile.restore_deadline = None
        profile.restored_at = now_datetime()
        profile.purge_status = ""
        profile.purge_started_at = None
        profile.purge_completed_at = None
        if hasattr(profile, "lifecycle_reason"):
            profile.lifecycle_reason = ""
        profile.save(ignore_permissions=True)
        frappe.db.set_value("User", user, "enabled", 1, update_modified=True)
        feature_summary = restore_deleted_account_features(user)
        account_log("account.restored", user=user)
        return {"status": ACCOUNT_STATUS_ACTIVE, "idempotent": False, "features": feature_summary}

    @staticmethod
    def _lock_profile(user: str):
        name = profile_name_for_user(user)
        if not name:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Profile` WHERE name = %s LIMIT 1 FOR UPDATE",
            (name,),
            as_dict=True,
        )
        if not rows:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        return frappe.get_doc("AOS Profile", name)
