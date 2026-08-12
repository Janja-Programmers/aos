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

from aos.services.social.repository import SocialRepository


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

    # Pair mutations lock User rows too. Holding the deleted account row for
    # this transaction prevents a concurrent follow/block from crossing the
    # cleanup boundary; mutation services revalidate lifecycle after waiting.
    frappe.db.sql(
        "SELECT name FROM `tabUser` WHERE name = %s FOR UPDATE",
        (user,),
    )

    now = now_datetime()
    sellers = _seller_names_for_user(user)

    summary: dict[str, int] = {}

    summary["active_calls_ended"] = _end_active_calls(user=user, now=now)
    summary["active_live_streams_ended"] = _end_active_live_streams(user=user, now=now)
    summary["live_view_sessions_closed"] = _close_live_view_rows(user=user, now=now)
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
    summary.update(_cleanup_verification_documents(user=user))
    summary["verification_requests_revoked"] = _revoke_verification_requests(user=user, now=now)
    summary["notifications_marked_read"] = _mark_notifications_read(user=user)

    # Chat history remains available to the other participant, but the deleted
    # account must not retain private personalization or remain an active inbox
    # participant. This is intentionally idempotent and bounded.
    summary.update(_cleanup_chat_private_state(user=user))

    # Reports are private reporter-owned moderation data. Remove rows submitted
    # by the deleted account while retaining reports about its public content or
    # account for staff audit. Ad aggregates are rebuilt from remaining rows.
    summary.update(_cleanup_report_account_data(user=user))

    # Marketplace trust history is retained and rendered through the Accounts
    # deleted-user serializer. Private reactions/reports are removed so deleted
    # accounts no longer keep personalization or reporter identity rows.
    summary.update(_cleanup_review_account_data(user=user))

    # Activity Center is private personalization/history, not retained audit.
    # Remove the deleted account's own history and redact/hide profile-history
    # snapshots owned by other users so deleted-account PII is not retained in
    # the presentation projection.
    summary.update(_cleanup_activity_account_data(user=user, sellers=sellers))

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

    count = _update_counted(
        "AOS Call",
        set_sql="""
            status = 'ended',
            is_active = 0,
            ended_at = COALESCE(ended_at, %s),
            ended_by = COALESCE(ended_by, %s),
            duration = CASE
                WHEN started_at IS NULL THEN GREATEST(COALESCE(duration, 0), 0)
                ELSE GREATEST(0, TIMESTAMPDIFF(SECOND, started_at, %s))
            END,
            room_cleanup_pending = 1,
            rtc_missing_since = NULL,
            modified = %s
        """,
        where_sql="""
            (caller = %s OR receiver = %s)
            AND (
                is_active = 1
                OR status IN ('initiated', 'ringing', 'ongoing')
            )
        """,
        set_params=(now, user, now, now),
        where_params=(user, user),
    )

    if count:
        # No provider I/O occurs inside account deletion. Queue a bounded set
        # after commit; the Calls reconciler drains any remainder durably.
        from aos.services.calls.livekit import enqueue_room_cleanup

        pending = frappe.get_all(
            "AOS Call",
            filters={
                "room_cleanup_pending": 1,
                "status": "ended",
                "caller": ["in", [user]],
            },
            pluck="name",
            order_by="modified asc, name asc",
            limit=50,
        )
        receiver_pending = frappe.get_all(
            "AOS Call",
            filters={
                "room_cleanup_pending": 1,
                "status": "ended",
                "receiver": ["in", [user]],
            },
            pluck="name",
            order_by="modified asc, name asc",
            limit=50,
        )
        for call_id in dict.fromkeys([*pending, *receiver_pending]):
            enqueue_room_cleanup(call_id)

    return count


def _end_active_live_streams(*, user: str, now) -> int:
    if not _doctype_exists("AOS Live Stream"):
        return 0

    total = 0
    while True:
        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live Stream`
            WHERE host_user = %s
              AND (is_active = 1 OR status IN ('scheduled', 'live'))
            ORDER BY creation ASC, name ASC
            LIMIT 100
            FOR UPDATE
            """,
            (user,),
            as_dict=True,
        )
        live_ids = [str(row.name) for row in rows if row.name]
        if not live_ids:
            return total

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream`
            SET status = 'ended',
                is_active = 0,
                active_host_key = NULL,
                ended_at = COALESCE(ended_at, %(ended_at)s),
                duration_seconds = GREATEST(
                    COALESCE(duration_seconds, 0),
                    COALESCE(
                        TIMESTAMPDIFF(SECOND, started_at, COALESCE(ended_at, %(ended_at)s)),
                        0
                    )
                ),
                room_cleanup_pending = 1,
                modified = %(modified)s
            WHERE name IN %(live_ids)s
            """,
            {"live_ids": tuple(live_ids), "ended_at": now, "modified": now},
        )
        for live_id in live_ids:
            try:
                frappe.enqueue(
                    "aos.tasks.live.cleanup_live_room",
                    live_id=live_id,
                    queue="short",
                    enqueue_after_commit=True,
                )
            except Exception:
                frappe.log_error(
                    "Live room cleanup enqueue failed.",
                    "Account deletion Live cleanup",
                )
        total += len(live_ids)


def _close_live_view_rows(*, user: str, now) -> int:
    if not _doctype_exists("AOS Live Stream View"):
        return 0

    from aos.services.live.participants import enqueue_view_removal

    from aos.services.live_analytics_service import LiveAnalyticsService

    total = 0
    while True:
        rows = frappe.db.sql(
            """
            SELECT v.name, v.live_stream, v.`user`
            FROM `tabAOS Live Stream View` v
            INNER JOIN `tabAOS Live Stream` l ON l.name = v.live_stream
            WHERE v.is_active = 1
              AND (v.user = %s OR l.host_user = %s)
            ORDER BY v.creation ASC, v.name ASC
            LIMIT 500
            FOR UPDATE
            """,
            (user, user),
            as_dict=True,
        )
        if not rows:
            break
        names = tuple(str(row.name) for row in rows)
        batch_live_ids = sorted({str(row.live_stream) for row in rows if row.live_stream})
        removal_ids = [str(row.name) for row in rows if str(row.user or "") == user]
        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream View`
            SET is_active = 0,
                active_identity_key = NULL,
                left_at = COALESCE(left_at, %(left_at)s),
                last_seen_at = %(left_at)s,
                watch_duration_seconds = GREATEST(
                    COALESCE(watch_duration_seconds, 0),
                    COALESCE(TIMESTAMPDIFF(SECOND, joined_at, %(left_at)s), 0),
                    0
                ),
                qualified = CASE
                    WHEN GREATEST(
                        COALESCE(watch_duration_seconds, 0),
                        COALESCE(TIMESTAMPDIFF(SECOND, joined_at, %(left_at)s), 0),
                        0
                    ) >= 5 THEN 1 ELSE 0
                END,
                modified = %(left_at)s
            WHERE name IN %(names)s
            """,
            {"names": names, "left_at": now},
        )
        for view_id in removal_ids:
            enqueue_view_removal(view_id)
        for live_id in batch_live_ids:
            LiveAnalyticsService.sync_view_metrics(live_id=live_id)
        total += len(rows)

    return total


def _close_live_cohost_rows(*, user: str, now) -> int:
    if not _doctype_exists("AOS Live CoHost"):
        return 0

    from aos.services.live.participants import enqueue_cohost_removal

    total = 0
    while True:
        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live CoHost`
            WHERE (
                    `user` = %s OR requested_by = %s OR responded_by = %s
                    OR live_stream IN (
                        SELECT name FROM `tabAOS Live Stream` WHERE host_user = %s
                    )
                  )
              AND status IN ('accepted', 'active')
            ORDER BY creation ASC, name ASC
            LIMIT 500
            FOR UPDATE
            """,
            (user, user, user, user),
            as_dict=True,
        )
        if not rows:
            break
        names = tuple(str(row.name) for row in rows)
        frappe.db.sql(
            """
            UPDATE `tabAOS Live CoHost`
            SET status = 'ended',
                is_active = 0,
                active_workflow_key = NULL,
                ended_at = COALESCE(ended_at, %(ended_at)s),
                ended_by = COALESCE(ended_by, %(ended_by)s),
                modified = %(modified)s
            WHERE name IN %(names)s
            """,
            {
                "names": names,
                "ended_at": now,
                "ended_by": user,
                "modified": now,
            },
        )
        for row in rows:
            enqueue_cohost_removal(str(row.name))
        total += len(rows)

    while True:
        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live CoHost`
            WHERE (
                    `user` = %s OR requested_by = %s OR responded_by = %s
                    OR live_stream IN (
                        SELECT name FROM `tabAOS Live Stream` WHERE host_user = %s
                    )
                  )
              AND status = 'pending'
            ORDER BY creation ASC, name ASC
            LIMIT 500
            FOR UPDATE
            """,
            (user, user, user, user),
            as_dict=True,
        )
        if not rows:
            break
        names = tuple(str(row.name) for row in rows)
        frappe.db.sql(
            """
            UPDATE `tabAOS Live CoHost`
            SET status = 'cancelled',
                is_active = 0,
                active_workflow_key = NULL,
                ended_at = COALESCE(ended_at, %(ended_at)s),
                ended_by = COALESCE(ended_by, %(ended_by)s),
                modified = %(modified)s
            WHERE name IN %(names)s
            """,
            {
                "names": names,
                "ended_at": now,
                "ended_by": user,
                "modified": now,
            },
        )
        total += len(rows)

    return total


def _mark_sellers_deleted(*, sellers: list[str]) -> int:
    if not sellers or not _doctype_exists("AOS Seller"):
        return 0

    where_sql = f"name IN ({_placeholders(sellers)}) AND status != 'Deleted'"

    now = now_datetime()
    return _update_counted(
        "AOS Seller",
        set_sql="""
            status = 'Deleted',
            status_reason_code = 'ACCOUNT_DELETED',
            status_source = 'accounts',
            status_changed_at = %s,
            total_ads = 0,
            modified = %s
        """,
        where_sql=where_sql,
        set_params=(now, now),
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


def _cleanup_verification_documents(*, user: str, batch_size: int = 250) -> dict[str, int]:
    """Release raw identity evidence while retaining the decision record.

    Account deletion is recoverable, but a restored account must resubmit fresh
    evidence from the existing Revoked state. Media is only orphaned here; the
    normal private-media cleanup job performs object-store deletion after the
    purpose's short retention window, avoiding storage I/O while account rows
    are locked. A failed media release retains its child reference so a later
    idempotent deletion/reconciliation pass can safely retry it.
    """
    summary = {
        "verification_documents_released": 0,
        "verification_document_rows_removed": 0,
    }
    if not (_doctype_exists("AOS Verification Request") and _doctype_exists("AOS Verification Document")):
        return summary

    from aos.services.media.media_service import MediaService

    request_names = frappe.get_all(
        "AOS Verification Request",
        filters={"user": user},
        pluck="name",
        order_by="name asc",
    )
    if not request_names:
        return summary

    service = MediaService()
    size = max(1, min(int(batch_size or 250), 500))
    cursor = ""
    while True:
        filters = {
            "parent": ["in", request_names],
            "parenttype": "AOS Verification Request",
        }
        if cursor:
            filters["name"] = [">", cursor]
        rows = frappe.get_all(
            "AOS Verification Document",
            filters=filters,
            fields=["name", "parent", "media"],
            order_by="name asc",
            limit_page_length=size,
        )
        if not rows:
            break

        removable_names: list[str] = []
        for row in rows:
            cursor = str(row.name)
            media_id = str(row.media or "").strip()
            if not media_id:
                removable_names.append(cursor)
                continue
            try:
                media = service.get_media_doc(media_id)
                if (
                    media.status == "Attached"
                    and media.attached_doctype == "AOS Verification Request"
                    and media.attached_name == row.parent
                ):
                    service.release_media(
                        media_id=media_id,
                        user=user,
                        attached_doctype="AOS Verification Request",
                        attached_name=row.parent,
                        system=True,
                    )
                    summary["verification_documents_released"] += 1
                elif media.status == "Attached":
                    # Never detach private media that points at a different
                    # resource. Keep this evidence row for explicit repair.
                    frappe.log_error(
                        "verification_document_attachment_mismatch",
                        "Account deletion Verification cleanup",
                    )
                    continue
                removable_names.append(cursor)
            except Exception:
                # Keep the sensitive relation if release cannot be confirmed;
                # losing the reference would make safe object cleanup harder.
                frappe.log_error(
                    "verification_document_release_failed",
                    "Account deletion Verification cleanup",
                )

        if removable_names:
            frappe.db.sql(
                "DELETE FROM `tabAOS Verification Document` WHERE name IN %s",
                (tuple(removable_names),),
            )
            summary["verification_document_rows_removed"] += len(removable_names)
        if len(rows) < size:
            break
    return summary


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



_CHAT_PRIVATE_USER_FIELDS = {
    "AOS Message Star": "user",
    "AOS Message Reaction": "user",
    "AOS Message Translation": "translated_by",
}


def _delete_chat_user_rows_bounded(*, doctype: str, field: str, user: str, batch_size: int = 500) -> int:
    # SQL identifiers cannot be parameterized. Keep this private helper locked to
    # the explicit Chat-private tables/columns above so caller input can never
    # influence an identifier. Values remain parameterized below.
    if _CHAT_PRIVATE_USER_FIELDS.get(doctype) != field:
        raise ValueError("Unsupported Chat cleanup target.")
    if not _doctype_exists(doctype) or not _has_field(doctype, field):
        return 0
    total = 0
    size = max(1, min(int(batch_size or 500), 1000))
    while True:
        rows = frappe.db.sql(
            f"SELECT name FROM {_table(doctype)} WHERE `{field}` = %s ORDER BY name LIMIT %s FOR UPDATE",
            (user, size),
            as_dict=True,
        )
        names = [str(row.name) for row in rows if row.name]
        if not names:
            return total
        frappe.db.sql(
            f"DELETE FROM {_table(doctype)} WHERE name IN %s",
            (tuple(names),),
        )
        total += len(names)


def _cleanup_chat_private_state(*, user: str) -> dict[str, int]:
    """Remove private Chat state without erasing shared conversation history."""

    summary = {
        "chat_stars_removed": _delete_chat_user_rows_bounded(
            doctype="AOS Message Star", field="user", user=user
        ),
        "chat_reactions_removed": _delete_chat_user_rows_bounded(
            doctype="AOS Message Reaction", field="user", user=user
        ),
        "chat_translation_cache_removed": _delete_chat_user_rows_bounded(
            doctype="AOS Message Translation", field="translated_by", user=user
        ),
        "chat_conversations_deactivated": 0,
    }

    if not _doctype_exists("AOS Conversation"):
        return summary

    total = 0
    while True:
        rows = frappe.db.sql(
            """
            SELECT name, participant_1, participant_2
            FROM `tabAOS Conversation`
            WHERE (participant_1 = %(user)s AND IFNULL(is_active_1, 1) = 1)
               OR (participant_2 = %(user)s AND IFNULL(is_active_2, 1) = 1)
            ORDER BY name
            LIMIT 500
            FOR UPDATE
            """,
            {"user": user},
            as_dict=True,
        )
        if not rows:
            break
        p1_ids = [str(row.name) for row in rows if row.participant_1 == user]
        p2_ids = [str(row.name) for row in rows if row.participant_2 == user]
        if p1_ids:
            frappe.db.sql(
                """UPDATE `tabAOS Conversation`
                SET is_active_1 = 0, unread_count_1 = 0
                WHERE name IN %(names)s""",
                {"names": tuple(p1_ids)},
            )
        if p2_ids:
            frappe.db.sql(
                """UPDATE `tabAOS Conversation`
                SET is_active_2 = 0, unread_count_2 = 0
                WHERE name IN %(names)s""",
                {"names": tuple(p2_ids)},
            )
        total += len(rows)
    summary["chat_conversations_deactivated"] = total
    return summary


def _cleanup_report_account_data(*, user: str) -> dict[str, int]:
    """Remove private reports submitted by a deleted account.

    Reports *about* the account/content are retained for moderation audit. This
    mirrors the existing Review-report privacy policy and keeps the caller's
    account-deletion transaction authoritative.
    """

    user_reports_removed = _delete_counted(
        "AOS User Report",
        where_sql="reported_by = %s",
        where_params=(user,),
    )
    short_reports_removed = _delete_counted(
        "AOS Short Report",
        where_sql="reported_by = %s",
        where_params=(user,),
    )

    ad_reports_removed = 0
    affected_ads: set[str] = set()
    if _doctype_exists("AOS Ad Report"):
        while True:
            rows = frappe.get_all(
                "AOS Ad Report",
                filters={"reported_by": user},
                fields=["name", "ad"],
                order_by="name asc",
                limit_page_length=250,
            )
            if not rows:
                break
            names = [str(row.name) for row in rows if row.name]
            affected_ads.update(str(row.ad) for row in rows if row.ad)
            if not names:
                break
            frappe.db.sql(
                "DELETE FROM `tabAOS Ad Report` WHERE name IN %(names)s",
                {"names": tuple(names)},
            )
            ad_reports_removed += len(names)

    for ad_id in sorted(affected_ads):
        if frappe.db.exists("AOS Ad", ad_id):
            total = frappe.db.count(
                "AOS Ad Report",
                {"ad": ad_id, "status": ["!=", "Rejected"]},
            )
            frappe.db.set_value(
                "AOS Ad", ad_id, "total_reports", int(total or 0), update_modified=False
            )

    return {
        "user_reports_removed": user_reports_removed,
        "short_reports_removed": short_reports_removed,
        "ad_reports_removed": ad_reports_removed,
        "ad_report_totals_recalculated": len(affected_ads),
    }


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


def _cleanup_activity_account_data(
    *,
    user: str,
    sellers: list[str] | None = None,
) -> dict[str, int]:
    """Remove private history and scrub retained snapshots for a deleted account."""
    if not _doctype_exists("AOS User Activity"):
        return {
            "activity_rows_removed": 0,
            "activity_profile_rows_redacted": 0,
            "activity_content_rows_redacted": 0,
        }

    own_rows = _delete_counted(
        "AOS User Activity",
        where_sql="user = %s",
        where_params=(user,),
    )

    public_id = ""
    try:
        from aos.services.accounts.identity import public_account_id_for_user

        public_id = str(public_account_id_for_user(user) or "").strip()
    except Exception:
        public_id = ""

    profile_clauses = ["(target_doctype = 'User' AND target_name = %s)"]
    profile_params: list[Any] = [user]
    if public_id:
        profile_clauses.append("(route_type = 'profile' AND route_id = %s)")
        profile_params.append(public_id)

    profile_where = " OR ".join(profile_clauses)
    profile_redacted = _redact_activity_history_rows(
        user=user,
        where_sql=f"({profile_where})",
        where_params=tuple(profile_params),
        title="Deleted account",
        subtitle="Profile",
    )

    # Activity snapshots are presentation data, not retained content archives.
    # When account deletion makes authored marketplace/video/live targets
    # unavailable, scrub those snapshots in other users' private history too.
    sellers = list(sellers or _seller_names_for_user(user))
    content_clauses: list[str] = []
    content_params: list[Any] = []

    if sellers and _doctype_exists("AOS Ad"):
        placeholders = _placeholders(sellers)
        content_clauses.append(
            f"(route_type = 'ad' AND route_id IN "
            f"(SELECT name FROM `tabAOS Ad` WHERE seller IN ({placeholders})))"
        )
        content_params.extend(sellers)

    if _doctype_exists("AOS Short"):
        short_owner_parts = ["owner = %s"]
        short_params: list[Any] = [user]
        if sellers:
            placeholders = _placeholders(sellers)
            short_owner_parts.append(f"seller IN ({placeholders})")
            short_params.extend(sellers)
        content_clauses.append(
            "(route_type = 'short' AND route_id IN "
            f"(SELECT name FROM `tabAOS Short` WHERE {' OR '.join(short_owner_parts)}))"
        )
        content_params.extend(short_params)

    if _doctype_exists("AOS Live Stream"):
        content_clauses.append(
            "(route_type = 'live' AND route_id IN "
            "(SELECT name FROM `tabAOS Live Stream` WHERE host_user = %s))"
        )
        content_params.append(user)

    content_redacted = 0
    if content_clauses:
        content_redacted = _redact_activity_history_rows(
            user=user,
            where_sql=f"({' OR '.join(content_clauses)})",
            where_params=tuple(content_params),
            title="Unavailable content",
            subtitle="Removed",
        )

    return {
        "activity_rows_removed": own_rows,
        "activity_profile_rows_redacted": profile_redacted,
        "activity_content_rows_redacted": content_redacted,
    }


def _redact_activity_history_rows(
    *,
    user: str,
    where_sql: str,
    where_params: tuple[Any, ...],
    title: str,
    subtitle: str,
) -> int:
    conditions = f"user != %s AND ({where_sql})"
    params = (user, *where_params)
    count = _count_rows("AOS User Activity", conditions, params)
    if count <= 0:
        return 0
    frappe.db.sql(
        f"""
        UPDATE `tabAOS User Activity`
        SET status = 'Hidden',
            active_key = NULL,
            target_title = %s,
            target_subtitle = %s,
            target_image = '',
            metadata_json = '{{}}'
        WHERE {conditions}
        """,
        (title, subtitle, *params),
    )
    return count

def _remove_social_graph(*, user: str) -> dict[str, int]:
    repository = SocialRepository()
    removed = 0
    recalculated = 0
    batch_size = 250

    if _doctype_exists("AOS Follow"):
        while True:
            rows = frappe.db.sql(
                """
                SELECT name, follower_user, following_user
                FROM `tabAOS Follow`
                WHERE follower_user = %s OR following_user = %s
                ORDER BY name ASC
                LIMIT %s
                """,
                (user, user, batch_size),
                as_dict=True,
            )
            if not rows:
                break

            names = tuple(str(row.name) for row in rows if row.name)
            if not names:
                break

            affected_users = {
                str(value)
                for row in rows
                for value in (row.follower_user, row.following_user)
                if value
            }
            frappe.db.sql(
                "DELETE FROM `tabAOS Follow` WHERE name IN %(names)s",
                {"names": names},
            )
            removed += len(names)
            repository.sync_counters(affected_users)
            recalculated += len(affected_users)

        repository.sync_counters([user])
        recalculated += 1

    blocks_closed = 0
    if _doctype_exists("AOS User Block"):
        now = now_datetime()
        while True:
            block_names = frappe.db.sql(
                """
                SELECT name
                FROM `tabAOS User Block`
                WHERE status = 'Active'
                  AND (blocker_user = %s OR blocked_user = %s)
                ORDER BY name ASC
                LIMIT %s
                """,
                (user, user, batch_size),
                pluck=True,
            )
            if not block_names:
                break

            names = tuple(str(name) for name in block_names if name)
            if not names:
                break

            frappe.db.sql(
                """
                UPDATE `tabAOS User Block`
                SET status = 'Unblocked',
                    unblocked_at = COALESCE(unblocked_at, %(now)s),
                    active_pair_key = NULL,
                    modified = %(now)s
                WHERE name IN %(names)s
                """,
                {"now": now, "names": names},
            )
            blocks_closed += len(names)

    return {
        "follow_rows_removed": removed,
        "profile_follow_totals_recalculated": recalculated,
        "active_social_blocks_closed": blocks_closed,
    }


def deactivate_account_features(user: str) -> dict[str, int]:
    """End ephemeral/realtime activity without hiding retained public content."""
    user = str(user or "").strip()
    if not user:
        return {}
    now = now_datetime()
    return {
        "active_calls_ended": _end_active_calls(user=user, now=now),
        "active_live_streams_ended": _end_active_live_streams(user=user, now=now),
        "live_view_sessions_closed": _close_live_view_rows(user=user, now=now),
        "live_cohost_rows_closed": _close_live_cohost_rows(user=user, now=now),
        "notifications_marked_read": _mark_notifications_read(user=user),
    }
