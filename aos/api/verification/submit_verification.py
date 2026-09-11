"""Submit or resubmit the current account's Verification request."""

from __future__ import annotations

import uuid

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.verification.errors import VerificationError
from aos.services.verification.service import VerificationService

from .constants import SUBMIT_VERIFICATION_LIMIT, SUBMIT_VERIFICATION_WINDOW_SECONDS


def submit_verification_impl(**kwargs):
    """Create or resubmit the authenticated account's Verification request."""

    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()

    rl = rate_limit(
        key=rate_limit_key("verification", "submit", "user", current_user),
        ttl_seconds=SUBMIT_VERIFICATION_WINDOW_SECONDS,
        limit=SUBMIT_VERIFICATION_LIMIT,
        message="Too many verification submissions. Please try again shortly.",
    )
    if rl:
        return rl

    savepoint = f"verification_submit_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        data = VerificationService().submit(user=current_user, payload=kwargs)
        return ok("Verification request submitted successfully.", data=data)
    except VerificationError as exc:
        frappe.db.rollback(save_point=savepoint)
        return safe_fail_from_exception(
            exc,
            fallback="Verification request could not be completed.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), "AOS Submit Verification Failed")
        return fail("Failed to submit verification.", error="VERIFICATION_INTERNAL_ERROR")
