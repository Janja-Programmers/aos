"""Locked, idempotent Accounts lifecycle orchestration."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.api.auth.session_control import SessionRevocationError, revoke_account_access
from aos.services.account_deletion_service import (
    cleanup_deleted_account_features,
    deactivate_account_features,
    restore_deleted_account_features,
)
from aos.services.media.media_service import MediaError, MediaService

from .constants import (
    ACCOUNT_RESTORE_WINDOW_DAYS,
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DEACTIVATED,
    ACCOUNT_STATUS_DELETED,
    PROFILE_DOCTYPE,
    PROFILE_IMAGE_FIELD,
)
from .errors import AccountConflictError, AccountNotFoundError
from .observability import account_log


class AccountLifecycleService:
    def __init__(self, media: MediaService | None = None):
        self.media = media or MediaService()

    def deactivate(self, *, user: str, reason: str = "") -> dict[str, Any]:
        profile = self._lock_profile(user)
        if profile.account_status == ACCOUNT_STATUS_DEACTIVATED:
            access = revoke_account_access(user)
            return {"status": ACCOUNT_STATUS_DEACTIVATED, "idempotent": True, **access}
        if profile.account_status == ACCOUNT_STATUS_DELETED or int(profile.is_deleted or 0):
            raise AccountConflictError("Deleted accounts cannot be deactivated.", code="ACCOUNT_DELETED")
        if profile.account_status != ACCOUNT_STATUS_ACTIVE:
            raise AccountConflictError("Account cannot be deactivated in its current state.")

        account_log("account.deactivation.requested", user=user)
        profile.account_status = ACCOUNT_STATUS_DEACTIVATED
        profile.deactivated_at = now_datetime()
        if hasattr(profile, "lifecycle_reason"):
            profile.lifecycle_reason = str(reason or "")[:300]
        profile.save(ignore_permissions=True)
        frappe.db.set_value("User", user, "enabled", 0, update_modified=True)
        feature_summary = deactivate_account_features(user)
        access = revoke_account_access(user)
        account_log("account.deactivated", user=user)
        return {
            "status": ACCOUNT_STATUS_DEACTIVATED,
            "idempotent": False,
            "features": feature_summary,
            **access,
        }

    def delete(self, *, user: str, reason: str = "") -> dict[str, Any]:
        profile = self._lock_profile(user)
        if profile.account_status == ACCOUNT_STATUS_DELETED or int(profile.is_deleted or 0):
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
        profile.is_deleted = 1
        profile.deleted_at = now
        profile.restore_deadline = add_to_date(now, days=ACCOUNT_RESTORE_WINDOW_DAYS)
        profile.delete_reason = str(reason or "")[:300]
        profile.deactivated_at = profile.deactivated_at or now
        profile.restored_at = None
        profile.is_verified = 0
        profile.verified_by = None
        profile.verified_on = None

        media_id = str(getattr(profile, PROFILE_IMAGE_FIELD, "") or "")
        if media_id:
            try:
                self.media.release_media(
                    media_id=media_id,
                    user=user,
                    attached_doctype=PROFILE_DOCTYPE,
                    attached_name=user,
                )
                profile.set(PROFILE_IMAGE_FIELD, "")
                frappe.db.set_value("User", user, "user_image", "", update_modified=False)
            except MediaError as exc:
                raise AccountConflictError("Avatar cleanup could not be completed.", code=getattr(exc, "code", "INVALID_STATE")) from exc

        profile.save(ignore_permissions=True)
        frappe.db.set_value("User", user, "enabled", 0, update_modified=True)
        feature_summary = cleanup_deleted_account_features(user)
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
        if profile.account_status == ACCOUNT_STATUS_ACTIVE and not int(profile.is_deleted or 0):
            return {"status": ACCOUNT_STATUS_ACTIVE, "idempotent": True}
        if profile.account_status != ACCOUNT_STATUS_DELETED and not int(profile.is_deleted or 0):
            raise AccountConflictError("Account is not deleted.", code="ACCOUNT_NOT_DELETED")
        if profile.restore_deadline and now_datetime() > profile.restore_deadline:
            raise AccountConflictError("This account can no longer be restored.", code="RESTORE_EXPIRED")
        profile.account_status = ACCOUNT_STATUS_ACTIVE
        profile.is_deleted = 0
        profile.deleted_at = None
        profile.delete_reason = ""
        profile.restore_deadline = None
        profile.deactivated_at = None
        profile.restored_at = now_datetime()
        if hasattr(profile, "lifecycle_reason"):
            profile.lifecycle_reason = ""
        profile.save(ignore_permissions=True)
        frappe.db.set_value("User", user, "enabled", 1, update_modified=True)
        feature_summary = restore_deleted_account_features(user)
        account_log("account.restored", user=user)
        return {"status": ACCOUNT_STATUS_ACTIVE, "idempotent": False, "features": feature_summary}

    @staticmethod
    def _lock_profile(user: str):
        if not user or not frappe.db.exists("AOS Profile", user):
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Profile` WHERE user = %s LIMIT 1 FOR UPDATE",
            (user,),
            as_dict=True,
        )
        if not rows:
            raise AccountNotFoundError("Account profile not found.", code="PROFILE_NOT_FOUND")
        return frappe.get_doc("AOS Profile", rows[0].name)
