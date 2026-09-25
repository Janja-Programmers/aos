"""Create or idempotently replay a canonical User report."""

from __future__ import annotations

import uuid

import frappe

from aos.api.reports.rate_limits import limit_report_submission
from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.reports.errors import ReportError
from aos.services.reports.service import ReportService


def report_user_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()
    limited = limit_report_submission(
        report_type="user",
        user=current_user,
        target_id=kwargs.get("account_id"),
    )
    if limited:
        return limited

    savepoint = f"report_user_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        data = ReportService().report_user(user=current_user, payload=kwargs)
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
        frappe.log_error(frappe.get_traceback(), "AOS Report User Failed")
        return fail("Failed to submit report.", error="INTERNAL_ERROR")
