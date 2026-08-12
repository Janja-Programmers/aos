"""Database boundaries for Report moderation.

Report review is performed through Frappe Desk rather than a bespoke reviewer API.
Every update therefore locks the authoritative report row before lifecycle checks so
concurrent/stale reviewers cannot overwrite a decision made by another reviewer.
"""

from __future__ import annotations

import frappe

_REPORT_TABLES = {
    "AOS User Report": "tabAOS User Report",
    "AOS Ad Report": "tabAOS Ad Report",
    "AOS Short Report": "tabAOS Short Report",
    "AOS Review Report": "tabAOS Review Report",
}


def locked_previous_report(doc):
    """Return the current persisted report under a row lock.

    New reports have no previous row. The DocType whitelist is intentionally
    fixed so a document-controlled value can never become a SQL identifier.
    The caller owns the surrounding transaction and commit/rollback.
    """

    if doc.is_new():
        return None
    table = _REPORT_TABLES.get(str(getattr(doc, "doctype", "") or ""))
    if not table:
        frappe.throw("Unsupported report type.", exc=frappe.ValidationError)
    name = str(getattr(doc, "name", "") or "").strip()
    if not name:
        frappe.throw("Report not found.", exc=frappe.DoesNotExistError)
    rows = frappe.db.sql(
        f"SELECT name FROM `{table}` WHERE name = %s LIMIT 1 FOR UPDATE",
        (name,),
    )
    if not rows:
        frappe.throw("Report not found.", exc=frappe.DoesNotExistError)
    return frappe.get_doc(doc.doctype, name)
