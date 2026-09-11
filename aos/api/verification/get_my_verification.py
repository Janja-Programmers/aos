"""Fetch the authenticated account's Verification status."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.verification.errors import VerificationError, VerificationValidationError
from aos.services.verification.service import VerificationService
from aos.services.verification.validation import strip_transport_fields

from .constants import GET_MY_VERIFICATION_LIMIT, GET_MY_VERIFICATION_WINDOW_SECONDS


def get_my_verification_impl(**kwargs):
    """Fetch logged-in user's privacy-safe Verification status."""

    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()

    rl = rate_limit(
        key=rate_limit_key("verification", "get_my", "user", current_user),
        ttl_seconds=GET_MY_VERIFICATION_WINDOW_SECONDS,
        limit=GET_MY_VERIFICATION_LIMIT,
        message="Too many verification status requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        unexpected = sorted(strip_transport_fields(kwargs))
        if unexpected:
            raise VerificationValidationError(
                f"Unsupported verification fields: {', '.join(unexpected)}.",
                code="VERIFICATION_UNKNOWN_FIELD",
            )
        data = VerificationService().get_my(user=current_user)
        return ok("Verification status fetched.", data=data)
    except VerificationError as exc:
        return safe_fail_from_exception(
            exc,
            fallback="Verification status could not be fetched.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Verification Failed")
        return fail("Failed to fetch verification status.", error="VERIFICATION_INTERNAL_ERROR")
