"""Harden human-report persistence, review queues, and duplicate integrity.

The existing model supports User, Ad, Short, and Review reports. Ad/Short/Review
uniqueness is already installed by their owning hardened domains. This patch
adds the missing User-report integrity boundary and the shared Report-reason
query index without deleting legitimate moderation history.
"""

from __future__ import annotations

import hashlib

import frappe

_BATCH_SIZE = 250

_INDEXES = {
    "idx_aos_user_report_backlog": ("AOS User Report", ["status", "creation", "name"]),
    "idx_aos_user_report_target": ("AOS User Report", ["reported_user", "status", "creation", "name"]),
    "idx_aos_user_report_reporter": ("AOS User Report", ["reported_by", "creation", "name"]),
    "idx_aos_report_reason_active": ("AOS Report Reason", ["is_active", "sort_order", "title"]),
}


def execute() -> None:
    for doctype in (
        "aos_report_reason",
        "aos_user_report",
        "aos_ad_report",
        "aos_short_report",
        "aos_review_report",
    ):
        frappe.reload_doc("aos", "doctype", doctype, force=True)

    _normalize_blank_statuses()
    duplicates = _reconcile_user_report_duplicates()
    _backfill_user_active_keys()

    for name, (doctype, fields) in _INDEXES.items():
        if _supports(doctype, fields) and not _index_exists(doctype, name):
            frappe.db.add_index(doctype, fields, index_name=name)

    if _supports("AOS User Report", ["active_key"]) and not _index_exists(
        "AOS User Report", "uq_aos_user_report_active", unique_only=True
    ):
        frappe.db.add_unique(
            "AOS User Report",
            ["active_key"],
            constraint_name="uq_aos_user_report_active",
        )

    frappe.logger("aos.reports", allow_site=True).info(
        "reports_schema_hardening_complete duplicate_user_reports_rejected=%s",
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


def _index_exists(doctype: str, name: str, *, unique_only: bool = False) -> bool:
    unique_clause = "AND NON_UNIQUE = 0" if unique_only else ""
    return bool(
        frappe.db.sql(
            f"""
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
              {unique_clause}
            LIMIT 1
            """,
            (f"tab{doctype}", name),
        )
    )
