from __future__ import annotations

from collections.abc import Iterable

import frappe
from frappe.utils import now_datetime


_SOCIAL_BATCH = 250


BASE_UNIQUE_CONSTRAINTS = [
    {
        "doctype": "AOS Follow",
        "fields": ["follower_user", "following_user"],
        "constraint_name": "unique_aos_follow_pair",
    },
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
        "doctype": "AOS Review Reaction",
        "fields": ["review", "user"],
        "constraint_name": "unique_aos_review_reaction_user",
    },
    {
        "doctype": "AOS User Block",
        "fields": ["active_pair_key"],
        "constraint_name": "unique_aos_user_block_active_pair",
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
    _dedupe_social_follows()
    _dedupe_short_comment_likes()
    _dedupe_review_reactions()
    _normalize_user_block_active_keys()
    _normalize_live_view_active_keys()
    _normalize_short_view_identity_keys()

    for constraint in UNIQUE_CONSTRAINTS:
        _add_unique_index(
            doctype=constraint["doctype"],
            fields=constraint["fields"],
            constraint_name=constraint["constraint_name"],
        )


# NORMALIZATION / DEDUPE

def _dedupe_social_follows():
    """Reconcile legacy duplicate follow edges before adding uniqueness."""
    if not _doctype_exists("AOS Follow"):
        return

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
                    (group.follower_user, group.following_user, keep_name, _SOCIAL_BATCH),
                    pluck=True,
                )
                if not stale:
                    break
                frappe.db.sql(
                    "DELETE FROM `tabAOS Follow` WHERE name IN %(names)s",
                    {"names": tuple(stale)},
                )

            frappe.db.sql(
                """
                UPDATE `tabAOS Profile` p
                SET total_followers = (
                        SELECT COUNT(*) FROM `tabAOS Follow` f WHERE f.following_user = p.user
                    ),
                    total_following = (
                        SELECT COUNT(*) FROM `tabAOS Follow` f WHERE f.follower_user = p.user
                    )
                WHERE p.user IN %(users)s
                """,
                {"users": (group.follower_user, group.following_user)},
            )


def _dedupe_short_comment_likes():
    affected_comments = _delete_duplicate_docs(
        doctype="AOS Short Comment Like",
        fields=["comment", "user"],
        order_by="modified desc, creation desc, name desc",
    )

    if affected_comments:
        _sync_short_comment_like_counts(affected_comments)


def _dedupe_reviews():
    """Deprecated non-destructive compatibility helper.

    Reviews are reconciled by ``harden_reviews_subsystem`` where duplicate
    records are retained and withdrawn deterministically. Historical versions
    deleted rows here; keeping this helper as a no-op prevents accidental data
    loss when older operational scripts still import it.
    """

    return set()


def _dedupe_review_reactions():
    affected_reviews = _delete_duplicate_docs(
        doctype="AOS Review Reaction",
        fields=["review", "user"],
        order_by="modified desc, creation desc, name desc",
    )

    if affected_reviews:
        _sync_review_reaction_counts(affected_reviews)


def _normalize_user_block_active_keys():
    doctype = "AOS User Block"
    if not _doctype_exists(doctype) or not _column_exists(doctype, "active_pair_key"):
        return

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
                    (group.blocker_user, group.blocked_user, keep_name, _SOCIAL_BATCH),
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
                    {"names": tuple(stale), "now": now_datetime()},
                )

    start_after = ""
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, blocker_user, blocked_user, status
            FROM `tabAOS User Block`
            WHERE name > %s
            ORDER BY name
            LIMIT %s
            """,
            (start_after, _SOCIAL_BATCH),
            as_dict=True,
        )
        if not rows:
            break

        for row in rows:
            key = (
                f"{row.blocker_user}|{row.blocked_user}"
                if row.status == "Active" and row.blocker_user and row.blocked_user
                else None
            )
            frappe.db.set_value(doctype, row.name, "active_pair_key", key, update_modified=False)
        start_after = rows[-1].name


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
            WHEN user IS NOT NULL AND user != '' THEN CONCAT('user:', user)
            WHEN session_id IS NOT NULL AND session_id != '' THEN CONCAT('session:', session_id)
            ELSE NULL
        END
        """
    )

    if affected_shorts:
        _sync_short_view_counts(affected_shorts)


def _dedupe_short_views_by_computed_identity() -> set[str]:
    """Dedupe legacy short views before writing identity_key.

    Existing sites may already have the unique index while older rows still
    carry NULL identity_key values. MariaDB allows duplicate NULL values in a
    unique index, so the migration must collapse duplicates by the *computed*
    identity before it updates identity_key. Updating first could violate the
    unique index on partially migrated sites.
    """

    rows = frappe.db.sql(
        """
        SELECT
            short,
            view_date,
            CASE
                WHEN user IS NOT NULL AND user != '' THEN CONCAT('user:', user)
                WHEN session_id IS NOT NULL AND session_id != '' THEN CONCAT('session:', session_id)
                ELSE NULL
            END AS computed_identity_key,
            GROUP_CONCAT(name ORDER BY watch_ms DESC, modified DESC, creation DESC, name DESC) AS names
        FROM `tabAOS Short View`
        WHERE short IS NOT NULL
          AND short != ''
          AND view_date IS NOT NULL
          AND (
              (user IS NOT NULL AND user != '')
              OR (session_id IS NOT NULL AND session_id != '')
          )
        GROUP BY
            short,
            view_date,
            CASE
                WHEN user IS NOT NULL AND user != '' THEN CONCAT('user:', user)
                WHEN session_id IS NOT NULL AND session_id != '' THEN CONCAT('session:', session_id)
                ELSE NULL
            END
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )

    affected_shorts: set[str] = set()

    for row in rows:
        names = _split_names(row.names)
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

        if row.short:
            affected_shorts.add(row.short)

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


def _sync_review_reaction_counts(review_ids: Iterable[str]):
    for review_id in set(review_ids):
        frappe.db.sql(
            """
            UPDATE `tabAOS Review` r
            SET
                like_count = (
                    SELECT COUNT(*)
                    FROM `tabAOS Review Reaction` rr
                    WHERE rr.review = r.name
                      AND rr.reaction = 'Like'
                ),
                dislike_count = (
                    SELECT COUNT(*)
                    FROM `tabAOS Review Reaction` rr
                    WHERE rr.review = r.name
                      AND rr.reaction = 'Dislike'
                )
            WHERE r.name = %s
            """,
            (review_id,),
        )


def _sync_review_metrics(ad_ids: Iterable[str]):
    for ad_id in set(ad_ids):
        result = frappe.db.sql(
            """
            SELECT AVG(rating) AS avg_rating, COUNT(*) AS total_reviews
            FROM `tabAOS Review`
            WHERE ad = %s
              AND status = 'Approved'
            """,
            (ad_id,),
            as_dict=True,
        )[0]

        frappe.db.set_value(
            "AOS Ad",
            ad_id,
            {
                "average_rating": round(result.avg_rating or 0, 2),
                "total_reviews": int(result.total_reviews or 0),
            },
            update_modified=False,
        )

        seller = frappe.db.get_value("AOS Ad", ad_id, "seller")
        if not seller:
            continue

        result = frappe.db.sql(
            """
            SELECT AVG(r.rating) AS avg_rating, COUNT(r.name) AS total_reviews
            FROM `tabAOS Review` r
            INNER JOIN `tabAOS Ad` a ON a.name = r.ad
            WHERE a.seller = %s
              AND r.status = 'Approved'
            """,
            (seller,),
            as_dict=True,
        )[0]

        frappe.db.set_value(
            "AOS Seller",
            seller,
            {
                "rating": round(result.avg_rating or 0, 2),
                "total_reviews": int(result.total_reviews or 0),
            },
            update_modified=False,
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
