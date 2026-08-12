"""Harden human-report persistence, review queues, and duplicate integrity.

The existing model supports User, Ad, Short, and Review reports. This data-only
patch reconciles legacy User-report duplicates and backfills the hidden active
key without deleting legitimate moderation history. Schema/index installation
is performed by the companion ``install_report_indexes`` patch.
"""

from __future__ import annotations

import hashlib

import frappe

_BATCH_SIZE = 250


def execute() -> None:
    # This is a post-model-sync data patch. Re-loading Report DocTypes here
    # is both unnecessary and unsafe: Frappe schema sync can remove manually
    # installed indexes owned by the Ads, Shorts, and Reviews domains. The
    # normal migrate model-sync phase has already applied source-controlled
    # DocType schema before this patch runs.
    _normalize_blank_statuses()
    duplicates = _reconcile_user_report_duplicates()
    _backfill_user_active_keys()

    frappe.logger("aos.reports", allow_site=True).info(
        "reports_data_hardening_complete duplicate_user_reports_rejected=%s",
        duplicates,
    )


def _normalize_blank_statuses() -> None:
    for doctype in ("AOS User Report", "AOS Ad Report", "AOS Short Report", "AOS Review Report"):
        if _supports(doctype, ["status"]):
            frappe.db.sql(
                f"UPDATE `tab{doctype}` SET status = 'Reviewing' WHERE COALESCE(status, '') = ''"
            )


def _user_key(target: str, reporter: str) -> str:
    material = f"{str(target or '').strip()}\x1f{str(reporter or '').strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _reconcile_user_report_duplicates() -> int:
    if not _supports("AOS User Report", ["reported_user", "reported_by", "status", "active_key"]):
        return 0
    rejected = 0
    while True:
        groups = frappe.db.sql(
            """
            SELECT reported_user, reported_by
            FROM `tabAOS User Report`
            WHERE status != 'Rejected'
              AND COALESCE(reported_user, '') != ''
              AND COALESCE(reported_by, '') != ''
            GROUP BY reported_user, reported_by
            HAVING COUNT(*) > 1
            ORDER BY reported_user, reported_by
            LIMIT %s
            """,
            (_BATCH_SIZE,),
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            rows = frappe.db.sql(
                """
                SELECT name
                FROM `tabAOS User Report`
                WHERE reported_user = %s AND reported_by = %s AND status != 'Rejected'
                ORDER BY creation ASC, name ASC
                """,
                (group.reported_user, group.reported_by),
                as_dict=True,
            )
            duplicates = [row.name for row in rows[1:]]
            if not duplicates:
                continue
            frappe.db.sql(
                """
                UPDATE `tabAOS User Report`
                SET status = 'Rejected', active_key = NULL, admin_action = NULL
                WHERE name IN %(names)s
                """,
                {"names": tuple(duplicates)},
            )
            rejected += len(duplicates)
    return rejected


def _backfill_user_active_keys() -> None:
    if not _supports("AOS User Report", ["reported_user", "reported_by", "status", "active_key"]):
        return
    frappe.db.sql("UPDATE `tabAOS User Report` SET active_key = NULL WHERE status = 'Rejected'")
    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, reported_user, reported_by
            FROM `tabAOS User Report`
            WHERE name > %s
              AND status != 'Rejected'
              AND COALESCE(reported_user, '') != ''
              AND COALESCE(reported_by, '') != ''
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _BATCH_SIZE),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            frappe.db.set_value(
                "AOS User Report",
                row.name,
                "active_key",
                _user_key(row.reported_user, row.reported_by),
                update_modified=False,
            )
        start_after = rows[-1].name


def _supports(doctype: str, fields: list[str]) -> bool:
    return bool(frappe.db.table_exists(doctype)) and all(
        frappe.db.has_column(doctype, field) for field in fields
    )

