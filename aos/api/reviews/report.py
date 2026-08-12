"""Review reporting API implementation."""

from __future__ import annotations

import uuid

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.transport import client_kwargs
from aos.services.accounts.http import set_private_no_store
from aos.services.reviews.api import review_fail, run_review_api  # compatibility export
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.errors import ReviewError
from aos.services.reviews.service import ReviewService


def report_review_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    set_private_no_store()
    limited = rate_limit(
        key=rate_limit_key("reviews", "report", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["report"],
        message="Too many review reports. Please try again later.",
    )
    if limited:
        return limited

    savepoint = f"report_review_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        data = ReviewService().report(user=user, payload=client_kwargs(kwargs))
        return ok("Review report submitted.", data=data)
    except ReviewError as exc:
        frappe.db.rollback(save_point=savepoint)
        return review_fail(exc, fallback="Failed to report review.")
    except frappe.DoesNotExistError:
        frappe.db.rollback(save_point=savepoint)
        from aos.services.reviews.errors import ReviewNotFoundError

        return review_fail(ReviewNotFoundError("Review not found."), fallback="Failed to report review.")
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), "AOS Report Review Failed")
        return fail("Failed to report review.", error="INTERNAL_ERROR", http_status=500)
