"""Production hardening for the mutual-follow Social graph.

The patch is idempotent, bounded, and does not commit. It preserves legitimate
relationships, removes invalid self/duplicate rows, normalizes active block
keys, reconciles counters, and installs query/uniqueness indexes.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

_BATCH = 250
_FOLLOW_INDEXES = {
    "idx_aos_follow_follower_page": ["follower_user", "creation", "name"],
    "idx_aos_follow_following_page": ["following_user", "creation", "name"],
    "idx_aos_follow_reverse_lookup": ["following_user", "follower_user", "creation"],
}
_BLOCK_INDEXES = {
    "idx_aos_block_blocker_page": ["blocker_user", "status", "blocked_at", "name"],
    "idx_aos_block_blocked_lookup": ["blocked_user", "status", "blocker_user"],
    "idx_aos_block_status_modified": ["status", "modified", "name"],
}
_PROFILE_INDEXES = {
    "idx_aos_profile_social_discovery": ["account_status", "is_verified", "total_followers", "name"],
}


def execute() -> None:
    frappe.reload_doc("aos", "doctype", "aos_follow", force=True)
    frappe.reload_doc("aos", "doctype", "aos_user_block", force=True)
    frappe.reload_doc("aos", "doctype", "aos_profile", force=True)
    if not frappe.db.table_exists("AOS Follow"):
        return
    _remove_self_follows()
    _remove_invalid_follows()
    _dedupe_follows()
    if frappe.db.table_exists("AOS User Block"):
        _normalize_blocks()
    _reconcile_profile_counters()
    _add_indexes("AOS Follow", _FOLLOW_INDEXES)
    if frappe.db.table_exists("AOS User Block"):
        _add_indexes("AOS User Block", _BLOCK_INDEXES)
    if frappe.db.table_exists("AOS Profile"):
        _add_indexes("AOS Profile", _PROFILE_INDEXES)
    _ensure_unique("AOS Follow", ["follower_user", "following_user"], "unique_aos_follow_pair")
    if frappe.db.table_exists("AOS User Block") and frappe.db.has_column("AOS User Block", "active_pair_key"):
        _ensure_unique("AOS User Block", ["active_pair_key"], "unique_aos_user_block_active_pair")


def _remove_self_follows() -> None:
    while True:
        rows = frappe.db.sql(
            """
            SELECT name FROM `tabAOS Follow`
            WHERE follower_user = following_user
            ORDER BY name LIMIT %s
            """,
            (_BATCH,),
            as_dict=True,
        )
        if not rows:
            break
        frappe.db.sql("DELETE FROM `tabAOS Follow` WHERE name IN %(names)s", {"names": tuple(row.name for row in rows)})


def _remove_invalid_follows() -> None:
    """Remove only orphaned edges. Deleted-account edges are recoverable state."""
    while True:
        rows = frappe.db.sql(
            """
            SELECT f.name
            FROM `tabAOS Follow` f
            LEFT JOIN `tabUser` follower ON follower.name = f.follower_user
            LEFT JOIN `tabUser` target ON target.name = f.following_user
            LEFT JOIN `tabAOS Profile` follower_profile ON follower_profile.user = f.follower_user
            LEFT JOIN `tabAOS Profile` target_profile ON target_profile.user = f.following_user
            WHERE follower.name IS NULL OR target.name IS NULL
               OR follower_profile.name IS NULL OR target_profile.name IS NULL
            ORDER BY f.name
            LIMIT %s
            """,
            (_BATCH,),
            as_dict=True,
        )
        if not rows:
            break
        frappe.db.sql(
            "DELETE FROM `tabAOS Follow` WHERE name IN %(names)s",
            {"names": tuple(row.name for row in rows)},
        )


def _dedupe_follows() -> None:
    while True:
        groups = frappe.db.sql(
            """
            SELECT follower_user, following_user
            FROM `tabAOS Follow`
            WHERE follower_user IS NOT NULL AND follower_user != ''
              AND following_user IS NOT NULL AND following_user != ''
            GROUP BY follower_user, following_user
            HAVING COUNT(*) > 1
            ORDER BY follower_user, following_user
            LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            keep = frappe.db.sql(
                """
                SELECT name FROM `tabAOS Follow`
                WHERE follower_user = %s AND following_user = %s
                ORDER BY creation ASC, name ASC
                LIMIT 1
                """,
                (group.follower_user, group.following_user),
                pluck=True,
            )
            keep_name = str(keep[0]) if keep else ""
            while keep_name:
                stale = frappe.db.sql(
                    """
                    SELECT name FROM `tabAOS Follow`
                    WHERE follower_user = %s AND following_user = %s AND name != %s
                    ORDER BY creation ASC, name ASC
                    LIMIT %s
                    """,
                    (group.follower_user, group.following_user, keep_name, _BATCH),
                    pluck=True,
                )
                if not stale:
                    break
                frappe.db.sql(
                    "DELETE FROM `tabAOS Follow` WHERE name IN %(names)s",
                    {"names": tuple(stale)},
                )


def _normalize_blocks() -> None:
    if not frappe.db.has_column("AOS User Block", "active_pair_key"):
        return
    # Invalid self-blocks become inactive history; they are not deleted.
    while True:
        rows = frappe.db.sql(
            """
            SELECT name FROM `tabAOS User Block`
            WHERE status = 'Active' AND blocker_user = blocked_user
            ORDER BY name LIMIT %s
            """,
            (_BATCH,),
            as_dict=True,
        )
        if not rows:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS User Block`
            SET status = 'Unblocked', unblocked_at = %(now)s, active_pair_key = NULL
            WHERE name IN %(names)s
            """,
            {"now": now_datetime(), "names": tuple(row.name for row in rows)},
        )

    # Only missing endpoints invalidate a block. Deleted-account blocks are
    # preserved through the restore window and remain a safety boundary.
    while True:
        rows = frappe.db.sql(
            """
            SELECT b.name
            FROM `tabAOS User Block` b
            LEFT JOIN `tabUser` blocker ON blocker.name = b.blocker_user
            LEFT JOIN `tabUser` blocked ON blocked.name = b.blocked_user
            LEFT JOIN `tabAOS Profile` blocker_profile ON blocker_profile.user = b.blocker_user
            LEFT JOIN `tabAOS Profile` blocked_profile ON blocked_profile.user = b.blocked_user
            WHERE b.status = 'Active'
              AND (
                  b.blocker_user IS NULL OR b.blocker_user = ''
                  OR b.blocked_user IS NULL OR b.blocked_user = ''
                  OR blocker.name IS NULL OR blocked.name IS NULL
                  OR blocker_profile.name IS NULL OR blocked_profile.name IS NULL
              )
            ORDER BY b.name
            LIMIT %s
            """,
            (_BATCH,),
            as_dict=True,
        )
        if not rows:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS User Block`
            SET status = 'Unblocked', unblocked_at = %(now)s, active_pair_key = NULL
            WHERE name IN %(names)s
            """,
            {"now": now_datetime(), "names": tuple(row.name for row in rows)},
        )

    # Unknown legacy statuses are made inactive in bounded batches without
    # destroying history or taking an unbounded write lock.
    while True:
        rows = frappe.db.sql(
            """
            SELECT name FROM `tabAOS User Block`
            WHERE status NOT IN ('Active', 'Unblocked') OR status IS NULL OR status = ''
            ORDER BY name LIMIT %s
            """,
            (_BATCH,),
            as_dict=True,
        )
        if not rows:
            break
        frappe.db.sql(
            """
            UPDATE `tabAOS User Block`
            SET status = 'Unblocked', unblocked_at = COALESCE(unblocked_at, modified), active_pair_key = NULL
            WHERE name IN %(names)s
            """,
            {"names": tuple(row.name for row in rows)},
        )

    while True:
        groups = frappe.db.sql(
            """
            SELECT blocker_user, blocked_user
            FROM `tabAOS User Block`
            WHERE status = 'Active'
              AND blocker_user IS NOT NULL AND blocker_user != ''
              AND blocked_user IS NOT NULL AND blocked_user != ''
            GROUP BY blocker_user, blocked_user
            HAVING COUNT(*) > 1
            ORDER BY blocker_user, blocked_user
            LIMIT 100
            """,
            as_dict=True,
        )
        if not groups:
            break
        for group in groups:
            keep = frappe.db.sql(
                """
                SELECT name FROM `tabAOS User Block`
                WHERE blocker_user = %s AND blocked_user = %s AND status = 'Active'
                ORDER BY modified DESC, creation DESC, name DESC
                LIMIT 1
                """,
                (group.blocker_user, group.blocked_user),
                pluck=True,
            )
            keep_name = str(keep[0]) if keep else ""
            while keep_name:
                stale = frappe.db.sql(
                    """
                    SELECT name FROM `tabAOS User Block`
                    WHERE blocker_user = %s AND blocked_user = %s
                      AND status = 'Active' AND name != %s
                    ORDER BY modified DESC, creation DESC, name DESC
                    LIMIT %s
                    """,
                    (group.blocker_user, group.blocked_user, keep_name, _BATCH),
                    pluck=True,
                )
                if not stale:
                    break
                frappe.db.sql(
                    """
                    UPDATE `tabAOS User Block`
                    SET status = 'Unblocked', unblocked_at = %(now)s, active_pair_key = NULL
                    WHERE name IN %(names)s
                    """,
                    {"now": now_datetime(), "names": tuple(stale)},
                )

    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, blocker_user, blocked_user, status
            FROM `tabAOS User Block`
            WHERE name > %s
            ORDER BY name LIMIT %s
            """,
            (start_after, _BATCH),
            as_dict=True,
        )
        if not rows:
            break
        for row in rows:
            key = f"{row.blocker_user}|{row.blocked_user}" if row.status == "Active" and row.blocker_user and row.blocked_user else None
            frappe.db.set_value("AOS User Block", row.name, "active_pair_key", key, update_modified=False)
        start_after = rows[-1].name


def _reconcile_profile_counters() -> None:
    if not frappe.db.table_exists("AOS Profile"):
        return
    start_after = ""
    while True:
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Profile` WHERE name > %s ORDER BY name LIMIT %s",
            (start_after, _BATCH),
            as_dict=True,
        )
        if not rows:
            break
        names = tuple(row.name for row in rows)
        frappe.db.sql(
            """
            UPDATE `tabAOS Profile` p
            SET total_followers = (SELECT COUNT(*) FROM `tabAOS Follow` f WHERE f.following_user = p.user),
                total_following = (SELECT COUNT(*) FROM `tabAOS Follow` f WHERE f.follower_user = p.user)
            WHERE p.name IN %(names)s
            """,
            {"names": names},
        )
        start_after = rows[-1].name


def _add_indexes(doctype: str, indexes: dict[str, list[str]]) -> None:
    for name, fields in indexes.items():
        if all(frappe.db.has_column(doctype, field) for field in fields) and not _index_exists(doctype, name):
            frappe.db.add_index(doctype, fields, index_name=name)


def _ensure_unique(doctype: str, fields: list[str], name: str) -> None:
    if all(frappe.db.has_column(doctype, field) for field in fields) and not _index_exists(doctype, name):
        frappe.db.add_unique(doctype, fields, constraint_name=name)


def _index_exists(doctype: str, name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT 1 FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
            LIMIT 1
            """,
            (f"tab{doctype}", name),
        )
    )
