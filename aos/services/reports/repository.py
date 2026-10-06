"""Database boundaries for report creation and Desk review."""

from __future__ import annotations

import hashlib

import frappe

from .constants import STATUS_REVIEWING

_REPORT_TABLES = {
    "AOS User Report": "tabAOS User Report",
    "AOS Ad Report": "tabAOS Ad Report",
    "AOS Short Report": "tabAOS Short Report",
    "AOS Review Report": "tabAOS Review Report",
}


def active_report_key(*, doctype: str, target_id: str, reporter: str) -> str:
    material = f"{doctype}\x1f{target_id}\x1f{reporter}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def find_reviewing_report(
    *,
    doctype: str,
    target_id: str,
    reporter: str,
    exclude_name: str | None = None,
    lock: bool = False,
):
    """Find the active duplicate, optionally with a current locking read.

    The normal pre-insert lookup is advisory only; the unique ``active_key`` is
    the cross-worker integrity boundary. ``lock=True`` is used after a duplicate
    key race so MariaDB returns the committed winner even under REPEATABLE READ.
    """

    table = _REPORT_TABLES.get(doctype)
    if not table:
        raise ValueError("Unsupported active-key report type")
    key = active_report_key(doctype=doctype, target_id=target_id, reporter=reporter)
    params: list[str] = [key, STATUS_REVIEWING]
    exclusion = ""
    if exclude_name:
        exclusion = " AND name != %s"
        params.append(str(exclude_name))
    locking = " FOR UPDATE" if lock else ""
    rows = frappe.db.sql(
        f"""
        SELECT name, reason, status, creation
        FROM `{table}`
        WHERE active_key = %s
          AND status = %s
          {exclusion}
        ORDER BY creation ASC, name ASC
        LIMIT 1{locking}
        """,
        tuple(params),
        as_dict=True,
    )
    return rows[0] if rows else None


def locked_previous_report(doc):
    """Return the current persisted report under a row lock."""

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
