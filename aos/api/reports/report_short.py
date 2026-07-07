from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.activity import record_short_report_activity

from .constants import (
    REPORT_SHORT_LIMIT_PER_MINUTE_PER_USER,
    SHORT_REPORT_DETAILS_MAX_LEN,
)


def _clean_text(value) -> str:
    return str(value or "").strip()


def _validate_reason(reason: str):
    if not reason:
        return fail("Reason is required.", code="VALIDATION_ERROR")

    reason_doc = frappe.db.get_value(
        "AOS Report Reason",
        reason,
        ["name", "is_active"],
        as_dict=True,
    )

    if not reason_doc:
        return fail("Invalid report reason.", code="VALIDATION_ERROR")

    if not int(reason_doc.is_active or 0):
        return fail("Selected report reason is inactive.", code="VALIDATION_ERROR")

    return None


def report_short_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:reports:short:user:{current_user}",
        ttl_seconds=60,
        limit=REPORT_SHORT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reports. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id") or kwargs.get("short"), "short_id")
    if err:
        return err

    reason = _clean_text(kwargs.get("reason"))
    details = _clean_text(kwargs.get("details"))

    err = _validate_reason(reason)
    if err:
        return err

    if len(details) > SHORT_REPORT_DETAILS_MAX_LEN:
        return fail(
            f"Details are too long. Maximum is {SHORT_REPORT_DETAILS_MAX_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    short = frappe.db.get_value(
        "AOS Short",
        short_id,
        ["name", "owner", "status", "visibility_status", "audience"],
        as_dict=True,
    )

    if not short:
        return fail("Short not found.", code="NOT_FOUND")

    if short.owner == current_user:
        return fail("You cannot report your own short.", code="VALIDATION_ERROR")

    if short.status != "ready" or short.visibility_status != "visible":
        return fail("Short not found.", code="NOT_FOUND")

    if not can_view_short(short, current_user=current_user):
        return fail("Short not found.", code="NOT_FOUND")

    if frappe.db.exists(
        "AOS Short Report",
        {
            "short": short_id,
            "reported_by": current_user,
            "status": ["!=", "Rejected"],
        },
    ):
        return fail("You have already reported this short.", code="VALIDATION_ERROR")

    try:
        report = frappe.new_doc("AOS Short Report")
        report.short = short_id
        report.short_owner = short.owner
        report.reported_by = current_user
        report.reason = reason
        report.details = details
        report.status = "Reviewing"

        report.insert(ignore_permissions=True)

        record_short_report_activity(
            user=current_user,
            short_id=short_id,
            report_id=report.name,
            reason=reason,
        )

        frappe.db.commit()

        return ok(
            "Report submitted successfully. Our team will review it.",
            data={"id": report.name, "short_id": short_id},
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Report Short Failed")
        frappe.db.rollback()
        return fail("Failed to submit report.", code="INTERNAL_ERROR")
