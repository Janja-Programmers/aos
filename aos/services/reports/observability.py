"""PII-safe structured logging for human report moderation."""

from __future__ import annotations

import frappe


def report_log(event: str, *, report_id: str | None = None, doctype: str | None = None, status: str | None = None, action: str | None = None, outcome: str = "success") -> None:
    try:
        frappe.logger("aos.reports", allow_site=True).info(
            "event=%s report_id=%s doctype=%s status=%s action=%s outcome=%s",
            str(event or "report.event")[:80],
            str(report_id or "")[:140],
            str(doctype or "")[:80],
            str(status or "")[:40],
            str(action or "")[:80],
            str(outcome or "")[:24],
        )
    except Exception:
        pass
