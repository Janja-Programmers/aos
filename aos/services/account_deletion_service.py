"""Feature cleanup for recoverable account deletion.

This service intentionally does not hard-delete the Frappe User or historical
records. It removes the deleted user from public/active surfaces while keeping
foreign-key style Link references valid for restore, audit, chats, calls, and
history.

Phase 2 policy:
- Hide/disable active public content owned by the user.
- End active realtime sessions involving the user.
- Remove private personalization and social graph rows.
- Do not automatically republish content on account restore.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime


ACCOUNT_DELETED_REASON = "Account deleted"


# Small DB helpers
def _doctype_exists(doctype: str) -> bool:
    try:
        return bool(frappe.db.exists("DocType", doctype))
    except Exception:
        return False


def _has_field(doctype: str, fieldname: str) -> bool:
    try:
        return bool(frappe.get_meta(doctype).has_field(fieldname))
    except Exception:
        return False


def _table(doctype: str) -> str:
    # Doctypes here are fixed internal strings, not user input.
    return f"`tab{doctype}`"


def _count_rows(doctype: str, where_sql: str, params: tuple[Any, ...]) -> int:
    if not _doctype_exists(doctype):
        return 0

    row = frappe.db.sql(
        f"SELECT COUNT(*) AS count FROM {_table(doctype)} WHERE {where_sql}",
        params,
        as_dict=True,
    )

    return int((row[0] or {}).get("count") or 0) if row else 0


def _update_counted(
    doctype: str,
    *,
    set_sql: str,
    where_sql: str,
    set_params: tuple[Any, ...] = (),
    where_params: tuple[Any, ...] = (),
) -> int:
    """Count matching rows first, then update them.

    Frappe's db.sql return value is not a portable row-count contract, so we
    count before update and return that count for endpoint summaries/logging.
    """
    count = _count_rows(doctype, where_sql, where_params)
    if count <= 0:
        return 0

    frappe.db.sql(
        f"UPDATE {_table(doctype)} SET {set_sql} WHERE {where_sql}",
        set_params + where_params,
    )

    return count


def _delete_counted(
    doctype: str,
    *,
    where_sql: str,
    where_params: tuple[Any, ...] = (),
) -> int:
    count = _count_rows(doctype, where_sql, where_params)
    if count <= 0:
        return 0

    frappe.db.sql(
        f"DELETE FROM {_table(doctype)} WHERE {where_sql}",
        where_params,
    )

    return count


def _seller_names_for_user(user: str) -> list[str]:
    if not _doctype_exists("AOS Seller"):
        return []

    sellers = frappe.get_all(
        "AOS Seller",
        filters={"user": user},
        pluck="name",
    )

    return [seller for seller in sellers if seller]


def _placeholders(values: list[str]) -> str:
    return ", ".join(["%s"] * len(values))


# Public entry points
def cleanup_deleted_account_features(user: str) -> dict[str, int]:
    """Hide/deactivate feature records for a newly deleted account.

    This is called inside the delete-account transaction. It does not commit.
    """
    user = (user or "").strip()
    if not user:
        return {}

    now = now_datetime()
    sellers = _seller_names_for_user(user)

    summary: dict[str, int] = {}

    summary["active_calls_ended"] = _end_active_calls(user=user, now=now)
    summary["active_live_streams_ended"] = _end_active_live_streams(user=user, now=now)
    summary["live_cohost_rows_closed"] = _close_live_cohost_rows(user=user, now=now)

    summary["seller_profiles_deleted"] = _mark_sellers_deleted(sellers=sellers)
    summary["ads_deleted"] = _mark_ads_deleted(sellers=sellers)
    summary["ad_drafts_abandoned"] = _abandon_ad_drafts(user=user)
    summary["shorts_deleted"] = _mark_shorts_deleted(user=user, sellers=sellers)

    # Public authored discussion surfaces. These are not auto-restored because
    # they may have been visible to many people and should stay hidden unless a
    # future moderation/restore workflow intentionally republishes them.
    summary.update(_mark_short_comments_deleted(user=user))
    summary.update(_mark_live_messages_deleted(user=user))

    summary["wishlist_items_removed"] = _remove_wishlist_items(user=user)
    summary["saved_searches_disabled"] = _disable_saved_searches(user=user)
    summary["verification_requests_revoked"] = _revoke_verification_requests(user=user, now=now)
    summary["notifications_marked_read"] = _mark_notifications_read(user=user)

    # Marketplace trust history is retained and rendered through the Accounts
    # deleted-user serializer. Private reactions/reports are removed so deleted
    # accounts no longer keep personalization or reporter identity rows.
    summary.update(_cleanup_review_account_data(user=user))

    follow_summary = _remove_social_graph(user=user)
    summary.update(follow_summary)

    return summary


def restore_deleted_account_features(user: str) -> dict[str, int]:
    """Phase 2 restore policy.

    Restore only reactivates login/profile in Phase 1. Public marketplace/video
    content is deliberately not republished automatically because listings,
    prices, videos, and seller trust state may be stale after deletion.
    """
    user = (user or "").strip()
    if not user:
        return {}

    return {
        "public_content_restored": 0,
        "seller_requires_reactivation": int(bool(_seller_names_for_user(user))),
    }


# Feature cleanup implementations
def _end_active_calls(*, user: str, now) -> int:
    if not _doctype_exists("AOS Call"):
        return 0

    return _update_counted(
        "AOS Call",
        set_sql="""
            status = 'ended',
            is_active = 0,
            ended_at = COALESCE(ended_at, %s),
            ended_by = COALESCE(ended_by, %s),
            modified = %s
        """,
        where_sql="""
            (caller = %s OR receiver = %s)
            AND (
                is_active = 1
                OR status IN ('initiated', 'ringing', 'ongoing')
            )
        """,
        set_params=(now, user, now),
        where_params=(user, user),
    )


def _end_active_live_streams(*, user: str, now) -> int:
    if not _doctype_exists("AOS Live Stream"):
        return 0

    return _update_counted(
        "AOS Live Stream",
        set_sql="""
            status = 'ended',
            is_active = 0,
            ended_at = COALESCE(ended_at, %s),
            modified = %s
        """,
        where_sql="""
            host_user = %s
            AND (
                is_active = 1
                OR status IN ('scheduled', 'live')
            )
        """,
        set_params=(now, now),
        where_params=(user,),
    )


def _close_live_cohost_rows(*, user: str, now) -> int:
    if not _doctype_exists("AOS Live CoHost"):
        return 0

    total = 0

    total += _update_counted(
        "AOS Live CoHost",
        set_sql="""
            status = 'ended',
            is_active = 0,
            ended_at = COALESCE(ended_at, %s),
            ended_by = COALESCE(ended_by, %s),
            modified = %s
        """,
        where_sql="""
            (user = %s OR requested_by = %s OR responded_by = %s)
            AND status IN ('accepted', 'active')
        """,
        set_params=(now, user, now),
        where_params=(user, user, user),
    )

    total += _update_counted(
        "AOS Live CoHost",
        set_sql="""
            status = 'cancelled',
            is_active = 0,
            ended_at = COALESCE(ended_at, %s),
            ended_by = COALESCE(ended_by, %s),
            modified = %s
        """,
        where_sql="""
            (user = %s OR requested_by = %s OR responded_by = %s)
            AND status IN ('pending')
        """,
        set_params=(now, user, now),
        where_params=(user, user, user),
    )

    return total


def _mark_sellers_deleted(*, sellers: list[str]) -> int:
    if not sellers or not _doctype_exists("AOS Seller"):
        return 0

    where_sql = f"name IN ({_placeholders(sellers)}) AND status != 'Deleted'"

    return _update_counted(
        "AOS Seller",
        set_sql="status = 'Deleted', modified = %s",
        where_sql=where_sql,
        set_params=(now_datetime(),),
        where_params=tuple(sellers),
    )


def _mark_ads_deleted(*, sellers: list[str]) -> int:
    if not sellers or not _doctype_exists("AOS Ad"):
        return 0

    where_sql = f"seller IN ({_placeholders(sellers)}) AND status != 'Deleted'"

    return _update_counted(
        "AOS Ad",
        set_sql="status = 'Deleted', modified = %s",
        where_sql=where_sql,
        set_params=(now_datetime(),),
        where_params=tuple(sellers),
    )


def _abandon_ad_drafts(*, user: str) -> int:
    if not _doctype_exists("AOS Ad Draft"):
        return 0

    return _update_counted(
        "AOS Ad Draft",
        set_sql="status = 'Abandoned', modified = %s",
        where_sql="user = %s AND status != 'Abandoned'",
        set_params=(now_datetime(),),
        where_params=(user,),
    )


def _mark_shorts_deleted(*, user: str, sellers: list[str]) -> int:
    if not _doctype_exists("AOS Short"):
        return 0

    now = now_datetime()
    total = 0

    total += _update_counted(
        "AOS Short",
        set_sql="""
            status = 'deleted',
            visibility_status = 'deleted',
            hidden_reason = %s,
            modified = %s
        """,
        where_sql="owner = %s AND status != 'deleted'",
        set_params=(ACCOUNT_DELETED_REASON, now),
        where_params=(user,),
    )

    if sellers:
        where_sql = f"seller IN ({_placeholders(sellers)}) AND status != 'deleted'"
        total += _update_counted(
            "AOS Short",
            set_sql="""
                status = 'deleted',
                visibility_status = 'deleted',
                hidden_reason = %s,
                modified = %s
            """,
            where_sql=where_sql,
            set_params=(ACCOUNT_DELETED_REASON, now),
            where_params=tuple(sellers),
        )

    return total


def _mark_short_comments_deleted(*, user: str) -> dict[str, int]:
    """Soft-delete the user's short comments and resync counters.

    This intentionally uses direct SQL for account deletion speed, so we must
    explicitly repair counters that the DocType controller would normally
    adjust when soft_delete() is called one document at a time.
    """
    if not _doctype_exists("AOS Short Comment"):
        return {
            "short_comments_deleted": 0,
            "short_comment_counters_synced": 0,
            "short_reply_counters_synced": 0,
        }

    rows = frappe.db.sql(
        """
        SELECT name, short, parent_comment, root_comment
        FROM `tabAOS Short Comment`
        WHERE user = %s AND status != 'deleted'
        """,
        (user,),
        as_dict=True,
    )

    short_ids: set[str] = set()
    root_comment_ids: set[str] = set()

    for row in rows:
        short_id = row.get("short")
        if short_id:
            short_ids.add(short_id)

        # AOS Short Comment.reply_count is maintained on the root comment.
        # Only replies affect reply_count. Top-level comments have no parent.
        if row.get("parent_comment"):
            root_id = row.get("root_comment") or row.get("parent_comment")
            if root_id:
                root_comment_ids.add(root_id)

    set_parts = ["status = 'deleted'", "modified = %s"]
    set_params: list[Any] = [now_datetime()]

    # Preserve schema compatibility if future/current installs have the field.
    if _has_field("AOS Short Comment", "comment"):
        set_parts.insert(1, "comment = ''")

    deleted = _update_counted(
        "AOS Short Comment",
        set_sql=", ".join(set_parts),
        where_sql="user = %s AND status != 'deleted'",
        set_params=tuple(set_params),
        where_params=(user,),
    )

    short_counter_syncs = _sync_short_comment_counts(short_ids)
    reply_counter_syncs = _sync_short_reply_counts(root_comment_ids)

    return {
        "short_comments_deleted": deleted,
        "short_comment_counters_synced": short_counter_syncs,
        "short_reply_counters_synced": reply_counter_syncs,
    }


def _sync_short_comment_counts(short_ids: set[str]) -> int:
    if not short_ids or not _doctype_exists("AOS Short"):
        return 0

    synced = 0

    for short_id in short_ids:
        if not short_id or not frappe.db.exists("AOS Short", short_id):
            continue

        count = frappe.db.count(
            "AOS Short Comment",
            {
                "short": short_id,
                "status": "active",
            },
        )

        frappe.db.set_value(
            "AOS Short",
            short_id,
            "comment_count",
            int(count or 0),
            update_modified=False,
        )

        synced += 1

    return synced


def _sync_short_reply_counts(root_comment_ids: set[str]) -> int:
    if not root_comment_ids:
        return 0

    synced = 0

    for root_id in root_comment_ids:
        if not root_id or not frappe.db.exists("AOS Short Comment", root_id):
            continue

        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Comment`
            WHERE root_comment = %s
              AND parent_comment IS NOT NULL
              AND parent_comment != ''
              AND status = 'active'
            """,
            (root_id,),
            as_dict=True,
        )

        count = int((result[0] or {}).get("count") or 0) if result else 0

        frappe.db.set_value(
            "AOS Short Comment",
            root_id,
            "reply_count",
            count,
            update_modified=False,
        )

        synced += 1

    return synced


def _mark_live_messages_deleted(*, user: str) -> dict[str, int]:
    """Soft-delete the user's live messages and resync counters.

    Direct SQL bypasses AOSLiveMessage.on_update(), so this repairs
    AOS Live Stream.comment_count and parent live-message reply_count.
    """
    if not _doctype_exists("AOS Live Message"):
        return {
            "live_messages_deleted": 0,
            "live_comment_counters_synced": 0,
            "live_reply_counters_synced": 0,
        }

    rows = frappe.db.sql(
        """
        SELECT name, live_stream, parent_message, message_kind, message_type
        FROM `tabAOS Live Message`
        WHERE user = %s AND status != 'deleted'
        """,
        (user,),
        as_dict=True,
    )

    live_ids: set[str] = set()
    parent_message_ids: set[str] = set()

    for row in rows:
        if (row.get("message_kind") or "").lower() != "comment":
            continue

        message_type = (row.get("message_type") or "").lower()
        if message_type not in {"comment", "reply"}:
            continue

        live_id = row.get("live_stream")
        if live_id:
            live_ids.add(live_id)

        if message_type == "reply" and row.get("parent_message"):
            parent_message_ids.add(row.get("parent_message"))

    set_parts = ["status = 'deleted'", "modified = %s"]
    set_params: list[Any] = [now_datetime()]

    if _has_field("AOS Live Message", "content"):
        set_parts.insert(1, "content = ''")

    deleted = _update_counted(
        "AOS Live Message",
        set_sql=", ".join(set_parts),
        where_sql="user = %s AND status != 'deleted'",
        set_params=tuple(set_params),
        where_params=(user,),
    )

    live_counter_syncs = _sync_live_comment_counts(live_ids)
    reply_counter_syncs = _sync_live_reply_counts(parent_message_ids)

    return {
        "live_messages_deleted": deleted,
        "live_comment_counters_synced": live_counter_syncs,
        "live_reply_counters_synced": reply_counter_syncs,
    }


def _sync_live_comment_counts(live_ids: set[str]) -> int:
    if not live_ids or not _doctype_exists("AOS Live Stream"):
        return 0

    synced = 0

    for live_id in live_ids:
        if not live_id or not frappe.db.exists("AOS Live Stream", live_id):
            continue

        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Live Message`
            WHERE live_stream = %s
              AND message_kind = 'comment'
              AND message_type IN ('comment', 'reply')
              AND status = 'active'
            """,
            (live_id,),
            as_dict=True,
        )

        count = int((result[0] or {}).get("count") or 0) if result else 0

        frappe.db.set_value(
            "AOS Live Stream",
            live_id,
            "comment_count",
            count,
            update_modified=False,
        )

        synced += 1

    return synced


def _sync_live_reply_counts(parent_message_ids: set[str]) -> int:
    if not parent_message_ids:
        return 0

    synced = 0

    for parent_id in parent_message_ids:
        if not parent_id or not frappe.db.exists("AOS Live Message", parent_id):
            continue

        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Live Message`
            WHERE parent_message = %s
              AND message_kind = 'comment'
              AND message_type = 'reply'
              AND status = 'active'
            """,
            (parent_id,),
            as_dict=True,
        )

        count = int((result[0] or {}).get("count") or 0) if result else 0

        frappe.db.set_value(
            "AOS Live Message",
            parent_id,
            "reply_count",
            count,
            update_modified=False,
        )

        synced += 1

    return synced


def _remove_wishlist_items(*, user: str) -> int:
    if not _doctype_exists("AOS Wishlist"):
        return 0

    affected_ads = frappe.get_all(
        "AOS Wishlist",
        filters={"user": user, "status": "Active"},
        pluck="ad",
        limit=0,
    )
    now = now_datetime()
    removed = _update_counted(
        "AOS Wishlist",
        set_sql="status = 'Removed', removed_on = %s, modified = %s",
        where_sql="user = %s AND status != 'Removed'",
        set_params=(now, now),
        where_params=(user,),
    )
    if affected_ads:
        from aos.services.wishlist.counters import recompute_wishlist_counts

        recompute_wishlist_counts(
            affected_ads,
            source="account_deletion_wishlist_cleanup",
        )
    return removed


def _disable_saved_searches(*, user: str) -> int:
    if not _doctype_exists("AOS Saved Search"):
        return 0

    return _update_counted(
        "AOS Saved Search",
        set_sql="is_active = 0, modified = %s",
        where_sql="user = %s AND is_active = 1",
        set_params=(now_datetime(),),
        where_params=(user,),
    )


def _revoke_verification_requests(*, user: str, now) -> int:
    if not _doctype_exists("AOS Verification Request"):
        return 0

    return _update_counted(
        "AOS Verification Request",
        set_sql="""
            status = 'Revoked',
            verified_on = COALESCE(verified_on, %s),
            rejection_reason = COALESCE(rejection_reason, %s),
            modified = %s
        """,
        where_sql="user = %s AND status IN ('Pending', 'Reviewing', 'Approved')",
        set_params=(now, ACCOUNT_DELETED_REASON, now),
        where_params=(user,),
    )


def _mark_notifications_read(*, user: str) -> int:
    if not _doctype_exists("AOS Notification"):
        return 0

    return _update_counted(
        "AOS Notification",
        set_sql="is_read = 1, modified = %s",
        where_sql="user = %s AND is_read = 0",
        set_params=(now_datetime(),),
        where_params=(user,),
    )



def _cleanup_review_account_data(*, user: str) -> dict[str, int]:
    """Apply the documented review-retention policy for a deleted account.

    Authored reviews remain as marketplace trust history. Public serialization
    resolves the deleted reviewer through Accounts and therefore exposes only an
    anonymized identity. Private reaction and report rows owned by the deleted
    account are removed, and affected reaction counters are rebuilt from source
    rows. The caller owns the surrounding account-deletion transaction.
    """

    authored_reviews = _count_rows("AOS Review", "reviewer = %s", (user,))

    affected_review_ids: list[str] = []
    if _doctype_exists("AOS Review Reaction"):
        rows = frappe.get_all(
            "AOS Review Reaction",
            filters={"user": user},
            pluck="review",
            limit=0,
        )
        affected_review_ids = sorted({str(review_id) for review_id in rows if review_id})

    reactions_removed = _delete_counted(
        "AOS Review Reaction",
        where_sql="user = %s",
        where_params=(user,),
    )
    reports_removed = _delete_counted(
        "AOS Review Report",
        where_sql="reported_by = %s",
        where_params=(user,),
    )

    counters_recalculated = 0
    if affected_review_ids:
        from aos.services.reviews.aggregates import recompute_review_reaction_counts

        for review_id in affected_review_ids:
            if frappe.db.exists("AOS Review", review_id):
                recompute_review_reaction_counts(review_id=review_id, lock_review=True)
                counters_recalculated += 1

    return {
        "reviews_retained_anonymized": authored_reviews,
        "review_reactions_removed": reactions_removed,
        "review_reports_removed": reports_removed,
        "review_reaction_totals_recalculated": counters_recalculated,
    }

def _remove_social_graph(*, user: str) -> dict[str, int]:
    if not _doctype_exists("AOS Follow"):
        return {
            "follow_rows_removed": 0,
            "profile_follow_totals_recalculated": 0,
        }

    rows = frappe.db.sql(
        """
        SELECT follower_user, following_user
        FROM `tabAOS Follow`
        WHERE follower_user = %s OR following_user = %s
        """,
        (user, user),
        as_dict=True,
    )

    affected_users: set[str] = {user}
    for row in rows:
        follower = row.get("follower_user")
        following = row.get("following_user")
        if follower:
            affected_users.add(follower)
        if following:
            affected_users.add(following)

    removed = _delete_counted(
        "AOS Follow",
        where_sql="follower_user = %s OR following_user = %s",
        where_params=(user, user),
    )

    recalculated = _recalculate_profile_follow_totals(affected_users)

    return {
        "follow_rows_removed": removed,
        "profile_follow_totals_recalculated": recalculated,
    }


def _recalculate_profile_follow_totals(users: set[str]) -> int:
    if not users or not _doctype_exists("AOS Profile"):
        return 0

    updated = 0

    for user in users:
        if not user or not frappe.db.exists("AOS Profile", user):
            continue

        total_followers = frappe.db.count(
            "AOS Follow",
            {"following_user": user},
        )
        total_following = frappe.db.count(
            "AOS Follow",
            {"follower_user": user},
        )

        frappe.db.set_value(
            "AOS Profile",
            user,
            {
                "total_followers": total_followers,
                "total_following": total_following,
            },
            update_modified=False,
        )

        updated += 1

    return updated


def deactivate_account_features(user: str) -> dict[str, int]:
    """End ephemeral/realtime activity without hiding retained public content."""
    user = str(user or "").strip()
    if not user:
        return {}
    now = now_datetime()
    return {
        "active_calls_ended": _end_active_calls(user=user, now=now),
        "active_live_streams_ended": _end_active_live_streams(user=user, now=now),
        "live_cohost_rows_closed": _close_live_cohost_rows(user=user, now=now),
        "notifications_marked_read": _mark_notifications_read(user=user),
    }
