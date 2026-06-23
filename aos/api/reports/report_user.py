from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.account_status import is_account_deleted
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.social.block import block_user_impl

from .constants import (
    REPORT_USER_LIMIT_PER_MINUTE_PER_USER,
    USER_REPORT_DETAILS_MAX_LEN,
)


def _clean_text(value) -> str:
    return str(value or "").strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value

    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _validate_target_user(*, current_user: str, target_user: str):
    if not target_user:
        return fail("Target user is required.", code="VALIDATION_ERROR")

    if target_user == current_user:
        return fail("You cannot report yourself.", code="VALIDATION_ERROR")

    user = frappe.db.get_value(
        "User",
        target_user,
        ["name", "enabled"],
        as_dict=True,
    )

    if not user:
        return fail("User not found.", code="NOT_FOUND")

    if int(user.enabled or 0) != 1:
        return fail("User not found.", code="NOT_FOUND")

    if is_account_deleted(target_user):
        return fail("User not found.", code="NOT_FOUND")

    if not frappe.db.exists("AOS Profile", target_user):
        return fail("User profile not found.", code="PROFILE_NOT_FOUND")

    return None


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


def report_user_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:reports:user_report:user:{current_user}",
        ttl_seconds=60,
        limit=REPORT_USER_LIMIT_PER_MINUTE_PER_USER,
        message="Too many reports. Please try again shortly.",
    )
    if rl:
        return rl

    target_user = _clean_text(kwargs.get("target_user") or kwargs.get("user"))
    reason = _clean_text(kwargs.get("reason"))
    details = _clean_text(kwargs.get("details"))
    should_block_user = _truthy(
        kwargs.get("block_user")
        if "block_user" in kwargs
        else kwargs.get("also_block")
    )

    err = _validate_target_user(
        current_user=current_user,
        target_user=target_user,
    )
    if err:
        return err

    err = _validate_reason(reason)
    if err:
        return err

    if len(details) > USER_REPORT_DETAILS_MAX_LEN:
        return fail(
            f"Details are too long. Maximum is {USER_REPORT_DETAILS_MAX_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    if frappe.db.exists(
        "AOS User Report",
        {
            "reported_user": target_user,
            "reported_by": current_user,
            "status": ["!=", "Rejected"],
        },
    ):
        return fail("You have already reported this user.", code="VALIDATION_ERROR")

    try:
        report = frappe.new_doc("AOS User Report")
        report.reported_user = target_user
        report.reported_by = current_user
        report.reason = reason
        report.details = details
        report.status = "Reviewing"

        report.insert(ignore_permissions=True)

        response_data = {
            "id": report.name,
            "block_requested": should_block_user,
            "block_applied": False,
            "block_status": None,
        }

        if should_block_user:
            # Preserve the report even if the optional block step fails.
            frappe.db.commit()

            block_response = block_user_impl(
                target_user=target_user,
                reason=f"Reported user: {reason}",
            )

            response_data["block_status"] = block_response.get("data")
            response_data["block_applied"] = bool(block_response.get("ok"))

            if not block_response.get("ok"):
                response_data["block_error"] = {
                    "message": block_response.get("message"),
                    "code": block_response.get("code"),
                }

        return ok(
            "Report submitted successfully. Our team will review it.",
            data=response_data,
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Report User Failed")
        return fail("Failed to submit report.", code="INTERNAL_ERROR")
