"""Backfill Wishlist lifecycle state, counters, and query indexes."""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime


_INDEX_NAME = "idx_aos_wishlist_user_status_saved"


def execute():
    if not _table_exists("AOS Wishlist") or not _table_exists("AOS Ad"):
        return

    now = now_datetime()
    _remove_own_ad_wishlist_rows(now=now)
    _backfill_lifecycle_timestamps(now=now)
    _backfill_ad_wishlist_counts()
    _add_saved_order_index()


def _remove_own_ad_wishlist_rows(*, now) -> None:
    frappe.db.sql(
        """
        UPDATE `tabAOS Wishlist` w
        INNER JOIN `tabAOS Ad` a ON a.name = w.ad
        INNER JOIN `tabAOS Seller` s ON s.name = a.seller
        SET w.status = 'Removed',
            w.removed_on = COALESCE(w.removed_on, %s),
            w.modified = %s
        WHERE w.status = 'Active'
          AND w.user = s.user
        """,
        (now, now),
    )


def _backfill_lifecycle_timestamps(*, now) -> None:
    frappe.db.sql(
        """
        UPDATE `tabAOS Wishlist`
        SET saved_on = COALESCE(saved_on, creation, %s)
        WHERE saved_on IS NULL
        """,
        (now,),
    )
    frappe.db.sql(
        """
        UPDATE `tabAOS Wishlist`
        SET removed_on = COALESCE(removed_on, modified, creation, %s)
        WHERE status = 'Removed'
          AND removed_on IS NULL
        """,
        (now,),
    )
    frappe.db.sql(
        """
        UPDATE `tabAOS Wishlist`
        SET removed_on = NULL
        WHERE status = 'Active'
        """
    )


def _backfill_ad_wishlist_counts() -> None:
    frappe.db.sql(
        """
        UPDATE `tabAOS Ad` a
        LEFT JOIN (
            SELECT ad, COUNT(*) AS active_count
            FROM `tabAOS Wishlist`
            WHERE status = 'Active'
            GROUP BY ad
        ) w ON w.ad = a.name
        SET a.wishlist_count = COALESCE(w.active_count, 0)
        """
    )


def _add_saved_order_index() -> None:
    if _index_exists("AOS Wishlist", _INDEX_NAME):
        return
    frappe.db.add_index(
        "AOS Wishlist",
        ["user", "status", "saved_on", "name"],
        index_name=_INDEX_NAME,
    )


def _table_exists(doctype: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}",),
        )
    )


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", index_name),
        )
    )
