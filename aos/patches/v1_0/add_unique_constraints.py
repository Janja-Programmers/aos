from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe.utils import now_datetime



BASE_UNIQUE_CONSTRAINTS = [
    {
        "doctype": "AOS Message Star",
        "fields": ["message", "user"],
        "constraint_name": "unique_aos_message_star_user",
    },
    {
        "doctype": "AOS Message Reaction",
        "fields": ["message", "user"],
        "constraint_name": "unique_aos_message_reaction_user",
    },
    {
        "doctype": "AOS Message Translation",
        "fields": ["message", "target_language", "original_content_hash"],
        "constraint_name": "unique_aos_message_translation_cache",
    },
    {
        "doctype": "AOS Short Like",
        "fields": ["short", "user"],
        "constraint_name": "unique_aos_short_like_user",
    },
    {
        "doctype": "AOS Short Save",
        "fields": ["short", "user"],
        "constraint_name": "unique_aos_short_save_user",
    },
    {
        "doctype": "AOS Sound Favorite",
        "fields": ["sound", "user"],
        "constraint_name": "unique_aos_sound_favorite_user",
    },
]


USER_ACTION_UNIQUE_CONSTRAINTS = [
    {
        "doctype": "AOS Short Comment Like",
        "fields": ["comment", "user"],
        "constraint_name": "unique_aos_short_comment_like_user",
    },
    {
        "doctype": "AOS Live Stream View",
        "fields": ["live_stream", "active_identity_key"],
        "constraint_name": "unique_aos_live_stream_view_active",
    },
    {
        "doctype": "AOS Short View",
        "fields": ["short", "view_date", "identity_key"],
        "constraint_name": "unique_aos_short_view_identity_day",
    },
]


UNIQUE_CONSTRAINTS = [
    *BASE_UNIQUE_CONSTRAINTS,
    *USER_ACTION_UNIQUE_CONSTRAINTS,
]


def execute():
    _dedupe_short_comment_likes()
    _normalize_live_view_active_keys()
    _normalize_short_view_identity_keys()

    for constraint in UNIQUE_CONSTRAINTS:
        _add_unique_index(
            doctype=constraint["doctype"],
            fields=constraint["fields"],
            constraint_name=constraint["constraint_name"],
        )


# NORMALIZATION / DEDUPE



def _dedupe_short_comment_likes():
    affected_comments = _delete_duplicate_docs(
        doctype="AOS Short Comment Like",
        fields=["comment", "user"],
        order_by="modified desc, creation desc, name desc",
    )

    if affected_comments:
        _sync_short_comment_like_counts(affected_comments)



def _normalize_live_view_active_keys():
    doctype = "AOS Live Stream View"
    if not _doctype_exists(doctype) or not _column_exists(doctype, "active_identity_key"):
        return

    rows = frappe.db.sql(
        """
        SELECT live_stream, session_id, GROUP_CONCAT(name ORDER BY modified DESC, creation DESC, name DESC) AS names
        FROM `tabAOS Live Stream View`
        WHERE is_active = 1
          AND live_stream IS NOT NULL
          AND live_stream != ''
          AND session_id IS NOT NULL
          AND session_id != ''
        GROUP BY live_stream, session_id
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )

    for row in rows:
        names = _split_names(row.names)
        stale_names = names[1:]
        if not stale_names:
            continue

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream View`
            SET
                is_active = 0,
                left_at = COALESCE(left_at, last_seen_at, joined_at, %(now)s),
                last_seen_at = COALESCE(last_seen_at, left_at, joined_at, %(now)s),
                active_identity_key = NULL
            WHERE name IN %(names)s
            """,
            {
                "names": tuple(stale_names),
                "now": now_datetime(),
            },
        )

    frappe.db.sql(
        """
        UPDATE `tabAOS Live Stream View`
        SET active_identity_key = CASE
            WHEN is_active = 1
                 AND session_id IS NOT NULL
                 AND session_id != ''
            THEN session_id
            ELSE NULL
        END
        """
    )


def _normalize_short_view_identity_keys():
    doctype = "AOS Short View"
    if not _doctype_exists(doctype) or not _column_exists(doctype, "identity_key"):
        return

    affected_shorts = _dedupe_short_views_by_computed_identity()

    frappe.db.sql(
        """
        UPDATE `tabAOS Short View`
        SET identity_key = CASE
            WHEN user IS NOT NULL AND user != ''
                THEN SHA2(CONCAT(short, '|user:', user), 256)
            WHEN session_id IS NOT NULL AND session_id != ''
                THEN SHA2(CONCAT(short, '|session:', session_id), 256)
            ELSE NULL
        END
        """
    )

    if affected_shorts:
        _sync_short_view_counts(affected_shorts)


def _dedupe_short_views_by_computed_identity() -> set[str]:
    """Dedupe legacy short views before writing identity_key.

    Existing sites may already have the globally unique ``identity_key`` field
    while older rows still carry NULL identity keys. Runtime writes hash the
    Short id together with the actor identity, so migration must use that exact
    algorithm too. Legacy duplicate rows for the same Short/actor are collapsed
    before the bulk UPDATE; otherwise assigning the canonical hash can violate
    the existing unique key on partially migrated sites.

    Do not use ``GROUP_CONCAT`` for document names here: its server-side length
    limit can silently truncate a large legacy duplicate set and leave rows that
    would still collide during normalization.
    """

    groups = frappe.db.sql(
        """
        SELECT
            short,
            CASE
                WHEN user IS NOT NULL AND user != '' THEN 'user'
                ELSE 'session'
            END AS actor_kind,
            CASE
                WHEN user IS NOT NULL AND user != '' THEN user
                ELSE session_id
            END AS actor_value,
            COUNT(*) AS row_count
        FROM `tabAOS Short View`
        WHERE short IS NOT NULL
          AND short != ''
          AND (
              (user IS NOT NULL AND user != '')
              OR (session_id IS NOT NULL AND session_id != '')
          )
        GROUP BY
            short,
            CASE
                WHEN user IS NOT NULL AND user != '' THEN 'user'
                ELSE 'session'
            END,
            CASE
                WHEN user IS NOT NULL AND user != '' THEN user
                ELSE session_id
            END
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )

    affected_shorts: set[str] = set()

    for group in groups:
        if group.actor_kind == "user":
            names = frappe.db.sql(
                """
                SELECT name
                FROM `tabAOS Short View`
                WHERE short = %(short)s
                  AND user = %(actor_value)s
                ORDER BY qualified DESC, watch_ms DESC, modified DESC, creation DESC, name DESC
                """,
                {"short": group.short, "actor_value": group.actor_value},
                pluck=True,
            )
        else:
            names = frappe.db.sql(
                """
                SELECT name
                FROM `tabAOS Short View`
                WHERE short = %(short)s
                  AND (user IS NULL OR user = '')
                  AND session_id = %(actor_value)s
                ORDER BY qualified DESC, watch_ms DESC, modified DESC, creation DESC, name DESC
                """,
                {"short": group.short, "actor_value": group.actor_value},
                pluck=True,
            )

        keeper = names[0] if names else None
        stale_names = names[1:]
        if not keeper or not stale_names:
            continue

        stats = frappe.db.sql(
            """
            SELECT
                MAX(COALESCE(watch_ms, 0)) AS watch_ms,
                MAX(qualified) AS qualified,
                MAX(last_seen_at) AS last_seen_at
            FROM `tabAOS Short View`
            WHERE name IN %(names)s
            """,
            {"names": tuple(names)},
            as_dict=True,
        )[0]

        frappe.db.set_value(
            "AOS Short View",
            keeper,
            {
                "watch_ms": int(stats.watch_ms or 0),
                "qualified": int(stats.qualified or 0),
                "last_seen_at": stats.last_seen_at,
            },
            update_modified=False,
        )

        frappe.db.sql(
            "DELETE FROM `tabAOS Short View` WHERE name IN %(names)s",
            {"names": tuple(stale_names)},
        )

        if group.short:
            affected_shorts.add(group.short)

    return affected_shorts


def _delete_duplicate_docs(*, doctype: str, fields: list[str], order_by: str) -> set[str]:
    if not _doctype_exists(doctype):
        return set()

    field_sql = ", ".join(f"`{field}`" for field in fields)
    not_empty_sql = " AND ".join(
        f"`{field}` IS NOT NULL AND `{field}` != ''" for field in fields
    )

    rows = frappe.db.sql(
        f"""
        SELECT {field_sql}, GROUP_CONCAT(name ORDER BY {order_by}) AS names
        FROM `tab{doctype}`
        WHERE {not_empty_sql}
        GROUP BY {field_sql}
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )

    affected_primary_values: set[str] = set()

    for row in rows:
        names = _split_names(row.names)
        stale_names = names[1:]
        if not stale_names:
            continue

        primary_value = row.get(fields[0])
        if primary_value:
            affected_primary_values.add(primary_value)

        for name in stale_names:
            frappe.delete_doc(
                doctype,
                name,
                ignore_permissions=True,
                force=True,
            )

    return affected_primary_values


def _add_unique_index(*, doctype: str, fields: list[str], constraint_name: str):
    if not _doctype_exists(doctype):
        frappe.log_error(
            title="Unique Constraint Patch Skipped",
            message=f"DocType {doctype} does not exist. Skipping {constraint_name}.",
        )
        return

    _validate_columns(doctype, fields)

    if _unique_index_exists(doctype, constraint_name):
        return

    frappe.db.add_unique(
        doctype,
        fields,
        constraint_name=constraint_name,
    )


def _validate_columns(doctype: str, fields: list[str]):
    missing = [field for field in fields if not _column_exists(doctype, field)]
    if missing:
        frappe.throw(
            f"Cannot add unique constraint on {doctype}. Missing columns: {', '.join(missing)}"
        )


def _doctype_exists(doctype: str) -> bool:
    return bool(frappe.db.exists("DocType", doctype))


def _column_exists(doctype: str, fieldname: str) -> bool:
    return bool(frappe.db.has_column(doctype, fieldname))


def _unique_index_exists(doctype: str, constraint_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
              AND INDEX_NAME = %s
              AND NON_UNIQUE = 0
            LIMIT 1
            """,
            (f"tab{doctype}", constraint_name),
            as_dict=True,
        )
    )


def _split_names(value: str | None) -> list[str]:
    return [name for name in str(value or "").split(",") if name]


def _sync_short_comment_like_counts(comment_ids: Iterable[str]):
    for comment_id in set(comment_ids):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short Comment` c
            SET like_count = (
                SELECT COUNT(*)
                FROM `tabAOS Short Comment Like` l
                WHERE l.comment = c.name
            )
            WHERE c.name = %s
            """,
            (comment_id,),
        )


def _sync_short_view_counts(short_ids: Iterable[str]):
    for short_id in set(short_ids):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short` s
            SET view_count = (
                SELECT COUNT(*)
                FROM `tabAOS Short View` v
                WHERE v.short = s.name
                  AND v.qualified = 1
            )
            WHERE s.name = %s
            """,
            (short_id,),
        )
