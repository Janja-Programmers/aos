"""List active report reasons for an authenticated account."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.reports.constants import REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER
from aos.services.reports.errors import ReportValidationError
from aos.services.reports.validation import ensure_known_fields, strip_transport_fields


def list_report_reasons_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    set_private_no_store()
    limited = rate_limit(
        key=rate_limit_key("reports", "reasons", "user", current_user),
        ttl_seconds=60,
        limit=REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many report-reason requests. Please try again shortly.",
    )
    if limited:
        return limited
    try:
        ensure_known_fields(strip_transport_fields(kwargs), frozenset())
        reasons = frappe.get_all(
            "AOS Report Reason",
            filters={"is_active": 1},
            fields=["name", "title", "icon_key", "sort_order"],
            order_by="sort_order asc, title asc",
            limit_page_length=200,
        )
        return ok(
            "Report reasons fetched.",
            data={
                "reasons": [
                    {"id": str(row.name), "title": str(row.title), "icon_key": row.icon_key}
                    for row in reasons
                ]
            },
        )
    except ReportValidationError:
        return fail("Invalid report request.", error="VALIDATION_ERROR", http_status=422)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Report Reasons Failed")
        return fail("Failed to fetch reasons.", error="INTERNAL_ERROR")
