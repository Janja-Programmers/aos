"""Create an Ads report with strict ownership and duplicate protection."""

from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.services.ads.constants import REPORT_AD_FIELDS
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import ensure_known_fields, normalize_identifier, normalize_report_details

from aos.api.ads.activity import record_ad_report_activity
from .constants import REPORT_AD_LIMIT_PER_MINUTE_PER_USER


def report_ad_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:reports:create:user:{user}",
        ttl_seconds=60,
        limit=REPORT_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    try:
        ensure_known_fields(kwargs, REPORT_AD_FIELDS)
        ad_id = normalize_identifier(kwargs.get("ad") or kwargs.get("ad_id"), field="ad_id", required=True)
        reason = normalize_identifier(kwargs.get("reason"), field="reason", required=True)
        details = normalize_report_details(kwargs.get("details"))
        if not frappe.db.exists("AOS Report Reason", {"name": reason, "is_active": 1}):
            return fail("Invalid report reason.", error="VALIDATION_ERROR")

        ad = frappe.db.get_value(
            "AOS Ad", ad_id, ["name", "seller", "status", "expires_on"], as_dict=True
        )
        if not ad or ad.status != "Active" or (ad.expires_on and getdate(ad.expires_on) < getdate(nowdate())):
            return fail("Ad not found.", error="AD_NOT_FOUND")
        seller = frappe.db.get_value("AOS Seller", ad.seller, ["user", "status"], as_dict=True)
        if not seller or seller.status != "Active":
            return fail("Ad not found.", error="AD_NOT_FOUND")
        if seller.user == user:
            return fail("You cannot report your own ad.", error="INVALID_AD_INPUT")

        # Lock the logical pair before checking. The migration's unique index is
        # the final concurrency guard if two first-time requests race.
        existing = frappe.db.sql(
            """
            SELECT name FROM `tabAOS Ad Report`
            WHERE ad = %s AND reported_by = %s
            ORDER BY creation ASC, name ASC
            LIMIT 1 FOR UPDATE
            """,
            (ad_id, user),
        )
        if existing:
            return fail("You have already reported this ad.", error="DUPLICATE", http_status=409)

        report = frappe.get_doc(
            {
                "doctype": "AOS Ad Report",
                "ad": ad_id,
                "reason": reason,
                "details": details,
                "reported_by": user,
                "seller": ad.seller,
            }
        )
        try:
            report.insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            return fail("You have already reported this ad.", error="DUPLICATE", http_status=409)
        record_ad_report_activity(user=user, ad_id=ad_id, report_id=report.name, reason=reason)
        return ok("Report submitted successfully. Our team will review it.", data={"id": report.name})
    except (AdsValidationError, ValueError, TypeError):
        return fail("Invalid report request.", error="VALIDATION_ERROR", http_status=422)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Create Report Failed")
        frappe.db.rollback()
        return fail("Failed to submit report.", error="INTERNAL_ERROR")
