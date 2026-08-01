"""Install Shorts indexes in a dedicated schema-only patch.

MariaDB DDL implicitly commits. Frappe therefore rejects ``ALTER TABLE`` after
transactional writes in the same patch. This patch must remain schema-only and
run immediately after ``harden_shorts_subsystem``, which performs all bounded
reconciliation in its own caller-managed transaction.

The patch is idempotent: completed indexes are detected through
``information_schema`` and skipped on retries.
"""
from __future__ import annotations

from collections.abc import Sequence

import frappe


INDEX_DEFINITIONS: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Short Metrics Daily", "uq_short_metrics_day", ("short", "date"), True),
    ("AOS Short Sound", "uq_short_sound_link", ("short",), True),
    ("AOS Short Report", "uq_short_report_active", ("active_key",), True),
    ("AOS Short Repost", "uq_short_repost_active", ("active_key",), True),
    ("AOS Video Processing Job", "uq_short_processing_active", ("active_key",), True),
    ("AOS Short Event", "uq_short_event_key", ("event_key",), True),
    (
        "AOS Short",
        "idx_short_feed",
        ("status", "visibility_status", "approval_status", "ranking_score", "posted_on", "name"),
        False,
    ),
    ("AOS Short", "idx_short_profile", ("owner", "status", "posted_on", "name"), False),
    (
        "AOS Short Comment",
        "idx_short_comment_page",
        ("short", "status", "parent_comment", "creation", "name"),
        False,
    ),
    ("AOS Short Report", "idx_short_report_review", ("status", "creation", "name"), False),
    (
        "AOS Video Processing Job",
        "idx_short_job_lifecycle",
        ("short", "status", "generation", "creation"),
        False,
    ),
)


def execute() -> None:
    """Install the reconciled Shorts uniqueness and query indexes."""
    for doctype, index_name, columns, unique in INDEX_DEFINITIONS:
        _ensure_index(doctype, index_name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _index_exists(doctype: str, index_name: str) -> bool:
    rows = frappe.db.sql(
        """SELECT 1
           FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA = DATABASE()
             AND TABLE_NAME = %s
             AND INDEX_NAME = %s
           LIMIT 1""",
        (_table(doctype), index_name),
    )
    return bool(rows)


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Shorts schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Shorts schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], index_name: str) -> None:
    non_null = " AND ".join(f"`{column}` IS NOT NULL" for column in columns)
    group_by = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""SELECT 1
            FROM `{_table(doctype)}`
            WHERE {non_null}
            GROUP BY {group_by}
            HAVING COUNT(*) > 1
            LIMIT 1"""
    )
    if duplicate:
        frappe.throw(
            f"Cannot install Shorts unique index {index_name}; duplicate data remains in {doctype}"
        )


def _ensure_index(
    doctype: str,
    index_name: str,
    columns: tuple[str, ...],
    *,
    unique: bool,
) -> None:
    _ensure_columns(doctype, columns)
    if _index_exists(doctype, index_name):
        return
    if unique:
        _assert_unique_ready(doctype, columns, index_name)

    kind = "UNIQUE INDEX" if unique else "INDEX"
    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    # This patch is intentionally DDL-only. Patch execution begins after Frappe
    # has committed the preceding data-reconciliation patch, so MariaDB's
    # implicit DDL commit cannot split domain data changes.
    frappe.db.sql(
        f"ALTER TABLE `{_table(doctype)}` ADD {kind} `{index_name}` ({quoted_columns})"
    )
