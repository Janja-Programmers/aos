"""Fetch target-scoped canonical report reasons for an authenticated account."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.reports.constants import (
    REASONS_FIELDS,
    REPORT_REASONS_LIMIT_PER_MINUTE_PER_USER,
)
from aos.services.reports.errors import ReportError
from aos.services.reports.validation import (
    ensure_known_fields,
    normalize_public_target_type,
    strip_transport_fields,
)


def get_report_reasons_impl(**kwargs):
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
        request = strip_transport_fields(kwargs)
        ensure_known_fields(request, REASONS_FIELDS)
        public_target, canonical_target = normalize_public_target_type(request.get("target_type"))
        reasons = frappe.db.sql(
            """
            SELECT r.name, r.label, r.description, r.icon_key, r.sort_order
            FROM `tabAOS Report Reason` r
            WHERE r.is_enabled = 1
              AND EXISTS (
                  SELECT 1
                  FROM `tabAOS Report Reason Target` t
                  WHERE t.parent = r.name
                    AND t.parenttype = 'AOS Report Reason'
                    AND t.parentfield = 'allowed_targets'
                    AND t.target_type = %s
              )
            ORDER BY r.sort_order ASC, r.label ASC, r.name ASC
            LIMIT 200
            """,
            (canonical_target,),
            as_dict=True,
        )
        return ok(
            "Report reasons fetched.",
            data={
                "target_type": public_target,
                "reasons": [
                    {
                        "id": str(row.name),
                        "label": str(row.label),
                        "description": str(row.description or ""),
                        "icon_key": str(row.icon_key or ""),
                    }
                    for row in reasons
                ],
            },
        )
    except ReportError as exc:
        return safe_fail_from_exception(
            exc,
            fallback="Invalid report request.",
            error=exc.code,
            http_status=exc.http_status,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Report Reasons Failed")
        return fail("Failed to fetch report reasons.", error="INTERNAL_ERROR")
