"""Recoverable account deletion and restoration endpoints."""

from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime

from aos.api.shared.account_status import (
    ACCOUNT_STATUS_ACTIVE,
    ACCOUNT_STATUS_DELETED,
    can_restore_account,
    deleted_account_response,
    get_account_state,
    is_account_deleted,
)
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail

from aos.services.account_deletion_service import (
    cleanup_deleted_account_features,
    restore_deleted_account_features,
)

from .constants import (
    ACCOUNT_RESTORE_WINDOW_DAYS,
    DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_IP,
    DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_USER,
    RESTORE_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
    RESTORE_REQUEST_LIMIT_PER_HOUR_PER_IP,
    RESTORE_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
    RESTORE_VERIFY_LIMIT_PER_HOUR_PER_IP,
)
from .otp_service import enforce_resend_cooldown, issue_otp, verify_otp
from .validators import normalize_email, validate_email
from .verification import ensure_ver_doc, get_ver_doc


RESTORE_PURPOSE = "account_restore"
DELETE_CONFIRMATION_TEXT = "DELETE"
DELETE_REASON_MAX_LEN = 300

RESTORE_REQUEST_GENERIC_MESSAGE = (
    "If a restorable account exists for this email, a restore code has been sent."
)


def _normalize_reason(value: str) -> str:
    reason = (value or "").strip()
    if len(reason) > DELETE_REASON_MAX_LEN:
        reason = reason[:DELETE_REASON_MAX_LEN]
    return reason


def _deactivate_push_tokens(user: str):
    """Deactivate all active push tokens for the user."""
    if not frappe.db.exists("DocType", "AOS Push Token"):
        return

    frappe.db.sql(
        """
        UPDATE `tabAOS Push Token`
        SET
            is_active = 0,
            last_used_at = %s
        WHERE user = %s
        """,
        (now_datetime(), user),
    )


def _expire_auth_tokens(user: str):
    """Expire OTP/reset/restore docs for this user.

    The restore request endpoint will create a fresh account_restore OTP later.
    """
    if not frappe.db.exists("DocType", "AOS Email Verification"):
        return

    frappe.db.sql(
        """
        UPDATE `tabAOS Email Verification`
        SET
            is_used = 1,
            reset_token_hash = '',
            reset_token_expires_at = NULL
        WHERE user = %s
        """,
        (user,),
    )


def _logout_current_session():
    try:
        frappe.local.login_manager.logout()
    except Exception:
        # Account deletion should not fail only because session cleanup failed.
        frappe.log_error(frappe.get_traceback(), "AOS Delete Account Logout Failed")


def _mark_profile_deleted(*, user: str, reason: str):
    profile = frappe.get_doc("AOS Profile", user)
    now = now_datetime()

    profile.account_status = ACCOUNT_STATUS_DELETED
    profile.is_deleted = 1
    profile.deleted_at = now
    profile.delete_reason = reason
    profile.restore_deadline = add_to_date(now, days=ACCOUNT_RESTORE_WINDOW_DAYS)
    profile.restored_at = None

    # Visible verification is revoked while the account is deleted.
    if hasattr(profile, "is_verified"):
        profile.is_verified = 0
    if hasattr(profile, "verified_by"):
        profile.verified_by = None
    if hasattr(profile, "verified_on"):
        profile.verified_on = None

    profile.save(ignore_permissions=True)


def _mark_profile_active(user: str):
    profile = frappe.get_doc("AOS Profile", user)
    profile.account_status = ACCOUNT_STATUS_ACTIVE
    profile.is_deleted = 0
    profile.deleted_at = None
    profile.delete_reason = ""
    profile.restore_deadline = None
    profile.restored_at = now_datetime()
    profile.save(ignore_permissions=True)


def delete_account_impl(**kwargs):
    """Soft-delete the current logged-in account.

    This disables login and hides the account, but keeps the same User record so
    old chats/calls/reviews/etc. remain referentially valid and restoration is possible.
    """
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:delete_account:user:{current_user}",
        ttl_seconds=60 * 60,
        limit=DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_USER,
        message="Too many delete-account attempts. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=f"aos:delete_account:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=DELETE_ACCOUNT_LIMIT_PER_HOUR_PER_IP,
        message="Too many delete-account attempts. Please try again later.",
    )
    if rl2:
        return rl2

    confirmation = (kwargs.get("confirmation") or "").strip()
    if confirmation != DELETE_CONFIRMATION_TEXT:
        return fail(
            "Please type DELETE to confirm account deletion.",
            code="VALIDATION_ERROR",
        )

    reason = _normalize_reason(kwargs.get("reason") or "")

    try:
        if is_account_deleted(current_user):
            _logout_current_session()
            frappe.db.commit()
            return ok("Account already deleted.")

        if not frappe.db.exists("AOS Profile", current_user):
            return fail("User profile not found.", code="PROFILE_NOT_FOUND", http_status=404)

        _mark_profile_deleted(user=current_user, reason=reason)
        cleanup_summary = cleanup_deleted_account_features(current_user)
        _deactivate_push_tokens(current_user)
        _expire_auth_tokens(current_user)

        frappe.db.set_value(
            "User",
            current_user,
            "enabled",
            0,
            update_modified=True,
        )

        _logout_current_session()
        frappe.db.commit()

        return ok(
            "Account deleted successfully. You can restore it with email verification within the restore window.",
            data={
                "restore_window_days": ACCOUNT_RESTORE_WINDOW_DAYS,
                "cleanup": cleanup_summary,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Delete Account Failed")
        frappe.db.rollback()
        return fail(
            "Failed to delete account. Please try again.",
            code="DELETE_ACCOUNT_FAILED",
            http_status=500,
        )


def request_restore_account_impl(**kwargs):
    """Send account-restore OTP for a deleted account.

    Guest endpoint. Response is intentionally generic to reduce account enumeration.
    """
    email = normalize_email(kwargs.get("email") or "")

    if not email:
        return fail("Email is required.", code="VALIDATION_ERROR")

    err = validate_email(email)
    if err:
        return err

    rl = rate_limit(
        key=f"aos:restore:req:email:{email}",
        ttl_seconds=60 * 60,
        limit=RESTORE_REQUEST_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many restore requests. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=f"aos:restore:req:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=RESTORE_REQUEST_LIMIT_PER_HOUR_PER_IP,
        message="Too many restore requests. Please try again later.",
    )
    if rl2:
        return rl2

    user_name = frappe.db.get_value("User", {"email": email}, "name")

    if not user_name:
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)

    state = get_account_state(user_name)
    if not state.get("is_deleted") or not state.get("can_restore"):
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)

    try:
        user = frappe.get_doc("User", user_name)
        ver = ensure_ver_doc(user_name, email=email, purpose=RESTORE_PURPOSE)

        cooldown = enforce_resend_cooldown(ver)
        if cooldown:
            return cooldown

        issue_otp(
            ver,
            email=email,
            full_name=user.first_name or user.full_name or "",
            purpose=RESTORE_PURPOSE,
        )

        frappe.db.commit()
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Request Restore Account Failed")
        frappe.db.rollback()
        return ok(RESTORE_REQUEST_GENERIC_MESSAGE)


def restore_account_impl(**kwargs):
    """Restore a soft-deleted account after account_restore OTP verification."""
    email = normalize_email(kwargs.get("email") or "")
    otp = (kwargs.get("otp") or "").strip()

    rl = rate_limit(
        key=f"aos:restore:verify:email:{email}",
        ttl_seconds=60 * 60,
        limit=RESTORE_VERIFY_LIMIT_PER_HOUR_PER_EMAIL,
        message="Too many restore attempts. Please try again later.",
    )
    if rl:
        return rl

    rl2 = rate_limit(
        key=f"aos:restore:verify:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=RESTORE_VERIFY_LIMIT_PER_HOUR_PER_IP,
        message="Too many restore attempts. Please try again later.",
    )
    if rl2:
        return rl2

    if not email or not otp:
        return fail("Email and OTP are required.", code="VALIDATION_ERROR")

    err = validate_email(email)
    if err:
        return err

    try:
        user_name = frappe.db.get_value("User", {"email": email}, "name")

        if not user_name:
            return fail("Invalid OTP.", code="OTP_INVALID")

        state = get_account_state(user_name)
        if not state.get("is_deleted"):
            return fail("Account is not deleted.", code="ACCOUNT_NOT_DELETED")

        if not can_restore_account(user_name):
            return fail(
                "This account can no longer be restored.",
                code="RESTORE_EXPIRED",
                data={"can_restore": False},
                http_status=410,
            )

        ver = get_ver_doc(user_name, purpose=RESTORE_PURPOSE)
        if not ver:
            return fail("OTP not found. Please request a new OTP.", code="OTP_NOT_FOUND")

        otp_err = verify_otp(ver, otp, consume=True)
        if otp_err:
            return otp_err

        _mark_profile_active(user_name)
        restore_summary = restore_deleted_account_features(user_name)

        frappe.db.set_value(
            "User",
            user_name,
            "enabled",
            1,
            update_modified=True,
        )

        frappe.db.commit()

        return ok(
            "Account restored successfully. Please login.",
            data={
                "can_login": True,
                "restore": restore_summary,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Restore Account Failed")
        frappe.db.rollback()
        return fail(
            "Failed to restore account. Please try again.",
            code="RESTORE_ACCOUNT_FAILED",
            http_status=500,
        )
