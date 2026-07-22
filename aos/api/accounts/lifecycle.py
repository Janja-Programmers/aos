"""Account deactivation API separate from recoverable deletion."""

from __future__ import annotations

import re

import frappe

from aos.api.auth.session_control import SessionRevocationError

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.accounts.errors import AccountError
from aos.services.accounts.http import set_private_no_store
from aos.services.accounts.lifecycle_service import AccountLifecycleService

from .constants import DEACTIVATE_ACCOUNT_LIMIT_PER_HOUR_PER_IP, DEACTIVATE_ACCOUNT_LIMIT_PER_HOUR_PER_USER

DEACTIVATE_CONFIRMATION_TEXT = "DEACTIVATE"


def deactivate_account_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    set_private_no_store()
    for key, limit in (
        (rate_limit_key("accounts", "deactivate", "user", user), DEACTIVATE_ACCOUNT_LIMIT_PER_HOUR_PER_USER),
        (rate_limit_key("accounts", "deactivate", "ip", request_ip()), DEACTIVATE_ACCOUNT_LIMIT_PER_HOUR_PER_IP),
    ):
        limited = rate_limit(key=key, ttl_seconds=3600, limit=limit, message="Too many deactivation attempts.")
        if limited:
            return limited
    confirmation = str(kwargs.get("confirmation") or "").strip()
    if confirmation != DEACTIVATE_CONFIRMATION_TEXT:
        return fail("Please type DEACTIVATE to confirm.", error="VALIDATION_ERROR")
    reason = re.sub(r"\s+", " ", str(kwargs.get("reason") or "").strip())[:300]
    try:
        data = AccountLifecycleService().deactivate(user=user, reason=reason)
        frappe.db.commit()
        try:
            frappe.local.login_manager.logout()
        except Exception:
            pass
        return ok("Account deactivated.", data=data)
    except AccountError as exc:
        frappe.db.rollback()
        return safe_fail_from_exception(
            exc,
            fallback="Account could not be deactivated.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except SessionRevocationError:
        frappe.db.rollback()
        return fail("Account access could not be revoked.", error="INTERNAL_ERROR")
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Deactivate Account Failed")
        return fail("Failed to deactivate account.", error="INTERNAL_ERROR")
