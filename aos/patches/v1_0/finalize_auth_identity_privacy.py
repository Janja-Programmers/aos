"""Migrate durable OIDC bindings to site-keyed names and erase retired raw identity PII."""

from __future__ import annotations

import frappe

from aos.aos.doctype.aos_auth_identity.aos_auth_identity import identity_name, user_provider_key


def execute() -> None:
    if frappe.db.table_exists("AOS Auth Identity"):
        _migrate_identity_names()
        _erase_retired_identity_columns()
    # Authentication challenges are intentionally ephemeral. If the retired
    # v10 table remains physically present, clear it rather than carrying old
    # OTP/reset state into the new challenge model.
    if frappe.db.table_exists("AOS Email Verification"):
        frappe.db.sql("DELETE FROM `tabAOS Email Verification`")


def _columns(doctype: str) -> set[str]:
    return {str(row[0]) for row in frappe.db.sql(f"SHOW COLUMNS FROM `tab{doctype}`")}


def _migrate_identity_names() -> None:
    columns = _columns("AOS Auth Identity")
    if "subject" not in columns:
        return
    rows = frappe.db.sql(
        "SELECT name, provider, user, subject FROM `tabAOS Auth Identity` ORDER BY name",
        as_dict=True,
    )
    for row in rows:
        provider = str(row.provider or "").strip().lower()
        user = str(row.user or "").strip()
        subject = str(row.subject or "").strip()
        if not provider or not user or not subject:
            frappe.throw("Invalid legacy authentication identity during migration.", frappe.ValidationError)
        target = identity_name(provider, subject)
        if str(row.name) != target:
            existing = frappe.db.get_value("AOS Auth Identity", target, "user")
            if existing and str(existing) != user:
                frappe.throw("Conflicting authentication identity during migration.", frappe.ValidationError)
            if existing:
                frappe.delete_doc("AOS Auth Identity", row.name, ignore_permissions=True, force=True)
            else:
                frappe.rename_doc("AOS Auth Identity", row.name, target, force=True)
        frappe.db.set_value(
            "AOS Auth Identity",
            target,
            "user_provider_key",
            user_provider_key(user, provider),
            update_modified=False,
        )


def _erase_retired_identity_columns() -> None:
    columns = _columns("AOS Auth Identity")
    assignments = []
    for field in ("subject", "email_at_link"):
        if field in columns:
            assignments.append(f"`{field}` = NULL")
    if assignments:
        frappe.db.sql(f"UPDATE `tabAOS Auth Identity` SET {', '.join(assignments)}")
