"""Recoverable account-deletion lifecycle helpers.

The 30-day deletion window is a reversible tombstone. Durable product data
(follow graph, blocks, seller/storefront, ads, Shorts, authored discussion,
chat/history, reviews, verification state, wishlist, saved searches,
notifications, Localization preferences and profile media) is preserved. Public
surfaces hide deleted/disabled owners through canonical account-status policy.

Only active realtime work and undelivered delivery work are terminated during
the grace window. Irreversible private-data cleanup runs later through
``aos.services.account_purge_service`` after the restore deadline.

The lower-level cleanup functions in this module are retained as permanent
purge primitives and are never invoked by the recoverable tombstone entry
point. None of these helpers commit.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime

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
def tombstone_deleted_account_features(user: str) -> dict[str, int]:
    """Apply the reversible 30-day account tombstone boundary.

    Durable user data is intentionally preserved during the restore window.
    The deleted account is hidden by the canonical User/AOS Profile lifecycle
    filters instead of rewriting potentially millions of related rows. Only
    active realtime work and undelivered push work are terminated here. The
    surrounding Accounts transaction owns commit/rollback.
    """
    user = (user or "").strip()
    if not user:
        return {}

    # Pair mutations also lock User rows. Holding this row prevents concurrent
    # follow/block/content mutations from crossing the account-status change.
    frappe.db.sql(
        "SELECT name FROM `tabUser` WHERE name = %s FOR UPDATE",
        (user,),
    )

    now = now_datetime()
    return {
        "active_calls_ended": _end_active_calls(user=user, now=now),
        "active_live_streams_ended": _end_active_live_streams(user=user, now=now),
        "live_view_sessions_closed": _close_live_view_rows(user=user, now=now),
        "live_cohost_rows_closed": _close_live_cohost_rows(user=user, now=now),
        "notification_delivery_jobs_cancelled": _cancel_notification_delivery_jobs(
            user=user, now=now
        ),
        "push_tokens_removed": _remove_push_tokens(user=user),
        "pending_media_uploads_cancelled": _cancel_pending_media_uploads(user=user, now=now),
        # These flags are explicit API/audit evidence that the large durable
        # domains were preserved rather than synchronously rewritten.
        "social_graph_preserved": 1,
        "marketplace_state_preserved": 1,
        "public_content_state_preserved": 1,
        "private_personalization_preserved": 1,
        "verification_state_preserved": 1,
    }


def restore_deleted_account_features(user: str) -> dict[str, int]:
    """Restore visibility of durable state by removing the account tombstone.

    Followers, following, blocks, seller/storefront state, ads, Shorts,
    comments, chats, reviews, verification, wishlist, saved searches,
    notifications, preferences and profile media were never rewritten during
    the grace window, so restoration is O(1) with respect to those domains.
    Active calls/Lives and revoked credentials are intentionally not recreated.
    """
    user = (user or "").strip()
    if not user:
        return {}
    return {
        "durable_state_restored": 1,
        "social_graph_restored": 1,
        "marketplace_state_restored": 1,
        "verification_state_restored": 1,
        "active_sessions_restored": 0,
        "active_realtime_sessions_restored": 0,
    }


# Feature cleanup implementations
def _end_active_calls(*, user: str, now) -> int:
    if not _doctype_exists("AOS Call") or not _doctype_exists("AOS Call Participant"):
        return 0

    rows = frappe.db.sql(
        """
        SELECT DISTINCT c.name,c.call_mode,c.status,c.started_at,c.initiator
        FROM `tabAOS Call` c
        INNER JOIN `tabAOS Call Participant` p ON p.`call`=c.name
        WHERE p.user=%s
          AND c.is_active=1
          AND c.status IN ('initiated','ringing','ongoing')
          AND p.status IN ('invited','ringing','joined')
        ORDER BY c.creation,c.name
        LIMIT 100
        FOR UPDATE
        """,
        (user,),
        as_dict=True,
    )
    if not rows:
        return 0

    from aos.services.calls.livekit import enqueue_room_cleanup

    terminalized: list[str] = []
    affected = 0
    for row in rows:
        frappe.db.sql(
            "SELECT name FROM `tabAOS Call Participant` WHERE `call`=%s ORDER BY user,name FOR UPDATE",
            (row.name,),
        )
        current = frappe.db.get_value(
            "AOS Call Participant", {"call": row.name, "user": user}, ["name", "status"], as_dict=True
        )
        if not current or current.status not in {"invited", "ringing", "joined"}:
            continue
        affected += 1

        if row.call_mode == "direct":
            duration = 0 if not row.started_at else max(0, int((now - get_datetime(row.started_at)).total_seconds()))
            frappe.db.sql(
                "UPDATE `tabAOS Call Participant` "
                "SET status=CASE WHEN status='joined' THEN 'left' ELSE 'cancelled' END, "
                "left_at=CASE WHEN status='joined' THEN COALESCE(left_at,%s) ELSE left_at END, "
                "responded_at=CASE WHEN status IN ('invited','ringing') THEN COALESCE(responded_at,%s) ELSE responded_at END "
                "WHERE `call`=%s AND status IN ('invited','ringing','joined')",
                (now, now, row.name),
            )
            frappe.db.sql(
                "UPDATE `tabAOS Call` SET status='ended',is_active=0,ended_at=COALESCE(ended_at,%s),"
                "ended_by=COALESCE(ended_by,%s),duration=%s,room_cleanup_pending=1,rtc_missing_since=NULL,"
                "state_version=COALESCE(state_version,1)+1,modified=%s WHERE name=%s AND is_active=1",
                (now, user, duration, now, row.name),
            )
            terminalized.append(row.name)
            continue

        # Group calls survive an initiator/member departure when other joined
        # participants remain. The initiator is not the conference lifetime owner.
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` "
            "SET status=CASE WHEN status='joined' THEN 'left' ELSE 'cancelled' END,"
            "left_at=CASE WHEN status='joined' THEN COALESCE(left_at,%s) ELSE left_at END,"
            "responded_at=CASE WHEN status IN ('invited','ringing') THEN COALESCE(responded_at,%s) ELSE responded_at END "
            "WHERE name=%s",
            (now, now, current.name),
        )
        joined = int(frappe.db.count("AOS Call Participant", {"call": row.name, "status": "joined"}) or 0)
        if joined > 0:
            frappe.db.sql(
                "UPDATE `tabAOS Call` SET state_version=COALESCE(state_version,1)+1,modified=%s WHERE name=%s",
                (now, row.name),
            )
            continue

        duration = 0 if not row.started_at else max(0, int((now - get_datetime(row.started_at)).total_seconds()))
        terminal_status = "ended" if row.status == "ongoing" else "cancelled"
        frappe.db.sql(
            "UPDATE `tabAOS Call Participant` SET status='cancelled',responded_at=COALESCE(responded_at,%s) "
            "WHERE `call`=%s AND status IN ('invited','ringing')",
            (now, row.name),
        )
        frappe.db.sql(
            "UPDATE `tabAOS Call` SET status=%s,is_active=0,ended_at=COALESCE(ended_at,%s),"
            "ended_by=COALESCE(ended_by,%s),duration=%s,room_cleanup_pending=1,rtc_missing_since=NULL,"
            "state_version=COALESCE(state_version,1)+1,modified=%s WHERE name=%s AND is_active=1",
            (terminal_status, now, user, duration, now, row.name),
        )
        terminalized.append(row.name)

    for call_id in dict.fromkeys(terminalized):
        enqueue_room_cleanup(call_id)
    return affected


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



def _cancel_pending_media_uploads(*, user: str, now) -> int:
    """Invalidate unconfirmed upload sessions without deleting durable media.

    Only ``Initialized`` objects are cancelled. Uploaded/processing/attached
    media belongs to durable product state and remains available for restore or
    its owning feature's normal lifecycle. No object-store call occurs while
    the account row is locked; the existing staging cleanup worker handles any
    leftover temporary object/multipart state.
    """
    if not _doctype_exists("AOS Media Object"):
        return 0
    count = _count_rows(
        "AOS Media Object",
        "owner_user = %s AND status = 'Initialized'",
        (user,),
    )
    if not count:
        return 0
    frappe.db.sql(
        """
        UPDATE `tabAOS Media Object`
        SET status = 'Failed',
            failure_code = 'ACCOUNT_DELETED',
            failure_reason = 'Upload session invalidated because the account was deleted',
            failed_at = %(now)s,
            upload_expires_at = %(now)s,
            staging_cleanup_required = 1,
            multipart_aborted_at = COALESCE(multipart_aborted_at, %(now)s),
            modified = %(now)s
        WHERE owner_user = %(user)s AND status = 'Initialized'
        """,
        {"user": user, "now": now},
    )
    return count

def _cleanup_verification_documents(*, user: str, batch_size: int = 250) -> dict[str, int]:
    """Release raw identity evidence while retaining the decision record.

    This helper is permanent-purge-only and therefore runs after the restore
    window is closed. Media is only orphaned here; the normal private-media
    cleanup job performs object-store deletion after the purpose's retention
    window, avoiding object-store I/O while account rows are locked. A failed
    media release retains its child reference so a later idempotent purge pass
    can safely retry it.
    """
    summary = {
        "verification_documents_released": 0,
        "verification_document_rows_removed": 0,
    }
    if not (_doctype_exists("AOS Verification Request") and _doctype_exists("AOS Verification Document")):
        return summary

    from aos.services.media.media_service import MediaNotFoundError, MediaService

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
            limit=size,
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
            except MediaNotFoundError:
                # The Media row is already absent, so this child relation is stale
                # and can be removed. Other Media/storage failures retain the
                # relation so cleanup can be retried safely.
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
            revoked_on = COALESCE(revoked_on, %s),
            rejection_reason = NULL,
            modified = %s
        """,
        where_sql="user = %s AND status IN ('Pending', 'Reviewing', 'Approved')",
        set_params=(now, now),
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


def _cancel_notification_delivery_jobs(
    *, user: str, now, reason: str = "recipient_account_deleted"
) -> int:
    """Neutralize all undelivered work while retaining terminal delivery audit rows."""
    reason = str(reason or "recipient_account_deleted").strip()[:120]
    if reason not in {"recipient_account_deleted", "recipient_account_deactivated"}:
        reason = "recipient_account_unavailable"
    if not _doctype_exists("AOS Notification Delivery Job"):
        return 0

    pending_statuses = ("Queued", "Dispatching", "Processing")
    total = 0
    while True:
        names = frappe.get_all(
            "AOS Notification Delivery Job",
            filters={"user": user, "status": ["in", list(pending_statuses)]},
            pluck="name",
            order_by="creation asc, name asc",
            limit=500,
        )
        if not names:
            return total

        frappe.db.sql(
            """
            UPDATE `tabAOS Notification Delivery Job`
            SET status = 'Cancelled', completed_at = %s, last_error = %s,
                request_payload = NULL
            WHERE name IN %s AND status IN ('Queued', 'Dispatching', 'Processing')
            """,
            (now, reason, tuple(names)),
        )

        if _doctype_exists("AOS Transactional Outbox"):
            frappe.db.sql(
                """
                UPDATE `tabAOS Transactional Outbox`
                SET status = 'Cancelled', completed_at = %s, next_attempt_at = NULL,
                    claimed_by = NULL, claim_token = NULL, claimed_at = NULL, lease_expires_at = NULL,
                    last_error = %s
                WHERE service_type = 'notification_delivery'
                  AND job_doctype = 'AOS Notification Delivery Job'
                  AND job_name IN %s
                  AND status NOT IN ('Completed', 'Completed With Failure', 'Failed', 'Dead Letter', 'Cancelled')
                """,
                (now, reason, tuple(names)),
            )
        total += len(names)


def _deactivate_push_tokens(*, user: str, now) -> int:
    """Disable provider registrations when an account loses login access."""
    if not _doctype_exists("AOS Push Token"):
        return 0
    count = _count_rows("AOS Push Token", "user = %s AND is_active = 1", (user,))
    if count:
        frappe.db.sql(
            """
            UPDATE `tabAOS Push Token`
            SET is_active = 0, active_device_key = NULL, last_used_at = %s
            WHERE user = %s AND is_active = 1
            """,
            (now, user),
        )
    return count


def _remove_push_tokens(*, user: str) -> int:
    """Provider registration identifiers are private state, not retained history."""
    if not _doctype_exists("AOS Push Token"):
        return 0
    return _delete_counted(
        "AOS Push Token",
        where_sql="user = %s",
        where_params=(user,),
    )



_CHAT_PRIVATE_USER_FIELDS = {
    "AOS Message Star": "user",
    "AOS Message Reaction": "user",
    "AOS Message Translation": "translated_by",
    "AOS Message User State": "user",
    "AOS Chat Lock Credential": "user",
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
    """Remove account-private Chat state and deactivate memberships without erasing shared history."""
    summary = {
        "chat_stars_removed": _delete_chat_user_rows_bounded(doctype="AOS Message Star", field="user", user=user),
        "chat_reactions_removed": _delete_chat_user_rows_bounded(doctype="AOS Message Reaction", field="user", user=user),
        "chat_translation_cache_removed": _delete_chat_user_rows_bounded(doctype="AOS Message Translation", field="translated_by", user=user),
        "chat_message_state_removed": _delete_chat_user_rows_bounded(doctype="AOS Message User State", field="user", user=user),
        "chat_lock_credentials_removed": _delete_chat_user_rows_bounded(doctype="AOS Chat Lock Credential", field="user", user=user),
        "chat_conversations_deactivated": 0,
    }
    if not _doctype_exists("AOS Conversation Participant"):
        return summary
    from aos.services.chat.membership import deactivate_account_memberships

    total = 0
    while True:
        count = deactivate_account_memberships(user, limit=500)
        total += count
        if count < 500:
            break
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
                limit=250,
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
        # Shorts have creator ownership only. Shop/listing authority remains in
        # hardened Ads through AOS Short Ad links; do not duplicate or infer a
        # seller column on the Short itself. A seller account deletion may make
        # its linked Ad unavailable, but a Short authored by another account is
        # still that creator's content and must not be redacted wholesale.
        content_clauses.append(
            "(route_type = 'short' AND route_id IN "
            "(SELECT name FROM `tabAOS Short` WHERE owner = %s))"
        )
        content_params.append(user)

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
