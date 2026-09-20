"""Create a Short report through the shared Report service."""

from __future__ import annotations

import uuid

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.shorts.activity import record_short_report_activity
from aos.services.accounts.http import set_private_no_store
from aos.services.reports.constants import REPORT_SHORT_LIMIT_PER_MINUTE_PER_USER
from aos.services.reports.errors import ReportError
from aos.services.reports.service import ReportService


def report_short_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()
    limited = rate_limit(
        key=rate_limit_key("reports", "short", "user", current_user),
        ttl_seconds=60,
        limit=REPORT_SHORT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reports. Please try again shortly.",
    )
    if limited:
        return limited

    savepoint = f"report_short_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        data = ReportService().report_short(
            user=current_user,
            payload=kwargs,
            activity_callback=record_short_report_activity,
        )
        return ok("Report submitted successfully. Our team will review it.", data=data)
    except ReportError as exc:
        frappe.db.rollback(save_point=savepoint)
        return safe_fail_from_exception(
            exc,
            fallback="Invalid report request.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), "AOS Report Short Failed")
        return fail("Failed to submit report.", error="INTERNAL_ERROR")
