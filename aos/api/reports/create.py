from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_country
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import CREATE_REPORT_LIMIT_PER_MINUTE_PER_USER


def create_report_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:reports:create:user:{current_user}",
        ttl_seconds=60,
        limit=CREATE_REPORT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad = kwargs.get("ad")
    reason = kwargs.get("reason")
    details = kwargs.get("details")

    if not ad:
        return fail("Ad is required.", code="VALIDATION_ERROR")

    if not reason:
        return fail("Reason is required.", code="VALIDATION_ERROR")

    # Market enforcement
    country, error = resolve_market_country(None)
    if error:
        return error

    ad_doc = frappe.db.get_value(
        "AOS Ad",
        ad,
        ["name", "seller", "status", "country"],
        as_dict=True
    )

    if not ad_doc:
        return fail("Ad not found.", code="NOT_FOUND")

    # Market isolation
    if ad_doc.country != country:
        return fail("Ad not found.", code="NOT_FOUND")

    if ad_doc.status in ("Deleted", "Suspended"):
        return fail("Ad not available.", code="NOT_FOUND")

    # Prevent reporting own ad
    seller_user = frappe.db.get_value("AOS Seller", ad_doc.seller, "user")

    if seller_user == current_user:
        return fail("You cannot report your own ad.", code="VALIDATION_ERROR")

    # Prevent duplicate report
    if frappe.db.exists(
        "AOS Ad Report",
        {
            "ad": ad,
            "reported_by": current_user
        }
    ):
        return fail("You have already reported this ad.", code="VALIDATION_ERROR")

    try:
        report = frappe.new_doc("AOS Ad Report")
        report.ad = ad
        report.reason = reason
        report.details = details
        report.reported_by = current_user
        report.seller = ad_doc.seller

        report.insert(ignore_permissions=True)

        return ok(
            "Report submitted successfully. Our team will review it.",
            data={"id": report.name}
        )

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Create Report Failed")
        return fail("Failed to submit report.", code="INTERNAL_ERROR")
