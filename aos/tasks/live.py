"""Background reconciliation and external room consistency for AOS Live."""

from __future__ import annotations

import time

import frappe
from frappe.utils import add_days, add_to_date, now_datetime

from aos.api.live.realtime import publish_viewer_count
from aos.services.livekit.admin import delete_room, ensure_room, list_participants, remove_participant
from aos.services.live.notifications import deliver_live_started_fanout, enqueue_live_started_fanout
from aos.services.live.participants import enqueue_view_removal
from aos.services.live.errors import LiveError
from aos.services.live.policy import LivePolicy
from aos.services.live.observability import live_log
from aos.services.live.constants import STATUS_ENDED, STATUS_FAILED, STATUS_LIVE, STATUS_STARTING
from aos.services.live_analytics_service import LiveAnalyticsService

BATCH_SIZE = 100


def materialize_hot_live_counters(*, live_id: str, delay_seconds: float = 0.35) -> dict[str, object]:
    """Persist Redis hot counters outside participant shared-lock transactions."""
    delay = max(0.0, min(float(delay_seconds or 0), 1.0))
    if delay:
        time.sleep(delay)
    row = frappe.db.get_value(
        "AOS Live Stream", live_id, ["status", "is_active"], as_dict=True
    )
    if not row:
        return {"ok": True, "outcome": "not_found"}
    if str(row.status or "") != "live" or not bool(row.is_active):
        return {"ok": True, "outcome": "not_active"}
    views = LiveAnalyticsService.materialize_view_metrics(live_id=live_id) or {}
    comments = LiveAnalyticsService.materialize_comment_count(live_id=live_id)
    reactions = LiveAnalyticsService.sync_reaction_count(live_id=live_id)
    return {
        "ok": True,
        "outcome": "materialized",
        "viewer_count": max(0, int(views.get("viewer_count") or 0)),
        "comment_count": max(0, int(comments or 0)),
        "reaction_count": max(0, int(reactions or 0)),
    }


def publish_coalesced_viewer_count(*, live_id: str, delay_seconds: float = 0.35) -> dict[str, object]:
    """Publish the trailing materialized viewer count for a coalesced burst."""
    row = frappe.db.get_value(
        "AOS Live Stream", live_id, ["status", "is_active"], as_dict=True
    )
    if not row:
        return {"ok": True, "outcome": "not_found"}
    if str(row.status or "") == "ended" or not bool(row.is_active):
        count = 0
    else:
        result = materialize_hot_live_counters(
            live_id=live_id, delay_seconds=delay_seconds
        )
        count = max(0, int(result.get("viewer_count") or 0))
    from aos.api.live.realtime import _publish_viewer_count_now

    _publish_viewer_count_now(live_id, count)
    return {"ok": True, "outcome": "published", "viewer_count": count}


def _enqueue_live_room_cleanup(live_id: str) -> None:
    try:
        frappe.enqueue(
            "aos.tasks.live.cleanup_live_room",
            queue="short",
            enqueue_after_commit=True,
            live_id=live_id,
        )
    except Exception:
        live_log("room_cleanup_enqueue", outcome="failure", reason="dependency")


def _mark_start_failed(*, live_id: str, reason: str) -> dict[str, object]:
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live Stream`
        WHERE name = %s
        LIMIT 1 FOR UPDATE
        """,
        (live_id,),
        as_dict=True,
    )
    if not rows:
        return {"ok": False, "outcome": "not_found"}
    live = frappe.get_doc("AOS Live Stream", live_id)
    if live.status != STATUS_STARTING:
        return {"ok": live.status == STATUS_LIVE, "outcome": str(live.status or "state_changed")}

    live.status = STATUS_FAILED
    # A timeout can be ambiguous: LiveKit may have created the room before the
    # response was lost. Always reconcile/delete the reserved room after a
    # failed provisioning attempt.
    live.room_cleanup_pending = 1
    live.room_provision_attempts = int(live.room_provision_attempts or 0) + 1
    live.last_room_error = str(reason or "unavailable")[:140]
    live.last_reconciled_at = now_datetime()
    live.save(ignore_permissions=True)
    _enqueue_live_room_cleanup(live.name)
    return {"ok": False, "outcome": str(reason or "unavailable")}


def activate_live_room(*, live_id: str) -> dict[str, object]:
    """Provision the external room before making a Live joinable.

    Room creation is intentionally outside the row lock. The second, locked
    state check prevents a concurrent end/cancel from being overwritten. If
    application state changed while provisioning, cleanup is scheduled rather
    than reviving the Live.
    """
    row = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["status", "host_user", "room_name"],
        as_dict=True,
    )
    if not row:
        return {"ok": False, "outcome": "not_found"}
    if row.status == STATUS_LIVE:
        return {"ok": True, "outcome": "already_live"}
    if row.status != STATUS_STARTING or not row.room_name:
        return {"ok": False, "outcome": "not_starting"}

    try:
        LivePolicy().require_account_available(row.host_user)
    except LiveError:
        live_log("room_provision", outcome="rejected", reason="host_unavailable")
        return _mark_start_failed(live_id=live_id, reason="host_unavailable")

    started = time.monotonic()
    result = ensure_room(row.room_name)
    latency_ms = int((time.monotonic() - started) * 1000)
    if not result.ok:
        live_log(
            "room_provision",
            outcome="failure",
            reason=result.category,
            latency_ms=latency_ms,
        )
        return _mark_start_failed(live_id=live_id, reason=result.category)

    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live Stream`
        WHERE name = %s
        LIMIT 1 FOR UPDATE
        """,
        (live_id,),
        as_dict=True,
    )
    if not rows:
        # The row disappeared after room creation. There is no canonical owner,
        # so remove the orphan directly.
        delete_room(row.room_name)
        return {"ok": False, "outcome": "deleted_during_create"}

    live = frappe.get_doc("AOS Live Stream", live_id)
    if live.status != STATUS_STARTING or live.room_name != row.room_name:
        if live.status in {STATUS_ENDED, STATUS_FAILED}:
            live.room_cleanup_pending = 1
            live.last_reconciled_at = now_datetime()
            live.save(ignore_permissions=True)
            _enqueue_live_room_cleanup(live.name)
        live_log(
            "room_provision_race",
            outcome="ignored",
            reason=str(live.status or "state_changed"),
            latency_ms=latency_ms,
        )
        return {"ok": False, "outcome": "state_changed"}

    live.status = STATUS_LIVE
    live.room_cleanup_pending = 0
    live.room_provision_attempts = int(live.room_provision_attempts or 0) + 1
    live.last_room_error = ""
    live.last_reconciled_at = now_datetime()
    live.save(ignore_permissions=True)
    live.reload()

    # Import lazily to keep background infrastructure independent from the
    # public API module during worker startup. These operations share the job
    # transaction; a DB failure rolls state back to `starting`, while the next
    # activation observes the already-created room idempotently.
    from aos.api.live.activity import record_live_host_activity
    from aos.api.live.live import _create_startup_messages
    from aos.api.live.realtime import publish_live_started

    record_live_host_activity(user=live.host_user, live_id=live.name)
    _create_startup_messages(live=live, host_user=live.host_user)
    publish_live_started(live)
    enqueue_live_started_fanout(live.name)

    live_log(
        "room_provision",
        outcome="success",
        reason=result.category,
        latency_ms=latency_ms,
    )
    return {"ok": True, "outcome": "live"}


def ensure_live_room(*, live_id: str) -> dict[str, object]:
    row = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["status", "is_active", "room_name"],
        as_dict=True,
    )
    if not row or row.status != "live" or not bool(row.is_active) or not row.room_name:
        return {"ok": True, "outcome": "not_active"}
    result = ensure_room(row.room_name)
    if result.ok:
        # The live may have ended while the external room creation request was
        # in flight. Re-read authoritative state before clearing cleanup intent;
        # otherwise an ended Live could retain a newly-created orphan room.
        current = frappe.db.get_value(
            "AOS Live Stream",
            live_id,
            ["status", "is_active", "room_name"],
            as_dict=True,
        )
        still_active = bool(
            current
            and current.status == "live"
            and bool(current.is_active)
            and current.room_name == row.room_name
        )
        if still_active:
            frappe.db.set_value(
                "AOS Live Stream",
                live_id,
                {"last_reconciled_at": now_datetime(), "room_cleanup_pending": 0},
                update_modified=False,
            )
        else:
            cleanup = delete_room(row.room_name)
            if current:
                frappe.db.set_value(
                    "AOS Live Stream",
                    live_id,
                    {
                        "last_reconciled_at": now_datetime(),
                        "room_cleanup_pending": 0 if cleanup.ok else 1,
                    },
                    update_modified=False,
                )
            live_log(
                "room_ensure_race_cleanup",
                outcome="idempotent" if cleanup.ok else "failure",
                reason=cleanup.category,
            )
            return {"ok": cleanup.ok, "outcome": "ended_during_create"}
    live_log("room_ensure", outcome="success" if result.ok else "failure", reason=result.category)
    return {"ok": result.ok, "outcome": result.category}


def cleanup_live_room(*, live_id: str) -> dict[str, object]:
    row = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["status", "is_active", "room_name", "room_cleanup_pending"],
        as_dict=True,
    )
    if not row or row.status not in {STATUS_ENDED, STATUS_FAILED} or bool(row.is_active):
        return {"ok": True, "outcome": "not_terminal"}
    if not row.room_name:
        frappe.db.set_value(
            "AOS Live Stream", live_id, "room_cleanup_pending", 0, update_modified=False
        )
        return {"ok": True, "outcome": "no_room"}
    result = delete_room(row.room_name)
    if result.ok:
        frappe.db.set_value(
            "AOS Live Stream",
            live_id,
            {"room_cleanup_pending": 0, "last_reconciled_at": now_datetime()},
            update_modified=False,
        )
    live_log("room_cleanup", outcome="success" if result.ok else "failure", reason=result.category)
    return {"ok": result.ok, "outcome": result.category}


def finalize_ended_live_views(*, live_id: str) -> dict[str, object]:
    """Close at most 500 terminal view rows per transaction/job.

    The next batch is queued after commit. If enqueueing fails,
    ``reconcile_live_state`` rediscovers the remaining active rows.
    """
    live = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["status", "is_active", "ended_at"],
        as_dict=True,
    )
    if not live or str(live.status or "") != "ended" or bool(live.is_active):
        return {"ok": True, "outcome": "not_ended", "closed": 0}

    ended_at = live.ended_at or now_datetime()
    names = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live Stream View`
        WHERE live_stream = %s AND is_active = 1
        ORDER BY creation ASC, name ASC
        LIMIT 500
        FOR UPDATE
        """,
        (live_id,),
        pluck=True,
    )
    if not names:
        LiveAnalyticsService.sync_view_metrics(live_id=live_id)
        LiveAnalyticsService.sync_comment_count(live_id=live_id)
        LiveAnalyticsService.sync_reaction_count(live_id=live_id)
        from aos.services.live.ephemeral import clear_comment_state, clear_reaction_state

        clear_comment_state(live_id=live_id)
        clear_reaction_state(live_id=live_id)
        publish_viewer_count(live_id, 0)
        return {"ok": True, "outcome": "complete", "closed": 0}

    frappe.db.sql(
        """
        UPDATE `tabAOS Live Stream View`
        SET is_active = 0,
            active_identity_key = NULL,
            left_at = COALESCE(left_at, %(ended_at)s),
            last_seen_at = %(ended_at)s,
            watch_duration_seconds = GREATEST(
                COALESCE(watch_duration_seconds, 0),
                COALESCE(TIMESTAMPDIFF(SECOND, joined_at, %(ended_at)s), 0),
                0
            ),
            qualified = CASE
                WHEN GREATEST(
                    COALESCE(watch_duration_seconds, 0),
                    COALESCE(TIMESTAMPDIFF(SECOND, joined_at, %(ended_at)s), 0),
                    0
                ) >= 5 THEN 1 ELSE 0
            END,
            modified = %(ended_at)s
        WHERE name IN %(names)s
        """,
        {"names": tuple(names), "ended_at": ended_at},
    )

    if len(names) < 500:
        LiveAnalyticsService.sync_view_metrics(live_id=live_id)
        LiveAnalyticsService.sync_comment_count(live_id=live_id)
        LiveAnalyticsService.sync_reaction_count(live_id=live_id)
        from aos.services.live.ephemeral import clear_comment_state, clear_reaction_state

        clear_comment_state(live_id=live_id)
        clear_reaction_state(live_id=live_id)
        publish_viewer_count(live_id, 0)
        return {"ok": True, "outcome": "complete", "closed": len(names)}

    try:
        frappe.enqueue(
            "aos.tasks.live.finalize_ended_live_views",
            queue="short",
            enqueue_after_commit=True,
            live_id=live_id,
        )
    except Exception:
        live_log("view_finalize_enqueue", outcome="failure", reason="dependency")
    return {"ok": True, "outcome": "continued", "closed": len(names)}


def _remove_tracked_participant(*, live_id: str, identity: str) -> dict[str, object]:
    row = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["room_name"],
        as_dict=True,
    )
    if not row or not row.room_name or not identity:
        return {"ok": True, "outcome": "not_found"}
    result = remove_participant(row.room_name, identity)
    live_log(
        "participant_removal",
        outcome="success" if result.ok else "failure",
        reason=result.category,
    )
    return {"ok": result.ok, "outcome": result.category}


def remove_live_cohost_participant(*, cohost_id: str) -> dict[str, object]:
    row = frappe.db.get_value(
        "AOS Live CoHost",
        cohost_id,
        ["live_stream", "livekit_identity"],
        as_dict=True,
    )
    if not row:
        return {"ok": True, "outcome": "not_found"}
    return _remove_tracked_participant(
        live_id=row.live_stream,
        identity=str(row.livekit_identity or ""),
    )


def remove_live_view_participant(*, view_id: str) -> dict[str, object]:
    row = frappe.db.get_value(
        "AOS Live Stream View",
        view_id,
        ["live_stream", "livekit_identity"],
        as_dict=True,
    )
    if not row:
        return {"ok": True, "outcome": "not_found"}
    return _remove_tracked_participant(
        live_id=row.live_stream,
        identity=str(row.livekit_identity or ""),
    )


def _close_missing_viewers(live_id: str, identities: tuple[str, ...]) -> int:
    """Close stale or newly unauthorized viewer rows in one bounded pass.

    Account and block state is resolved in the same parameterized query so a
    reconciliation pass does not perform one Social lookup per participant.
    Guests remain authorized by account policy, while authenticated viewers
    must still be enabled, active, undeleted, and unblocked in both directions.
    """
    rows = frappe.db.sql(
        """
        SELECT v.name, v.livekit_identity, v.last_seen_at, v.joined_at, v.`user`,
               CASE
                   WHEN v.`user` IS NULL OR v.`user` = '' THEN 1
                   WHEN COALESCE(u.enabled, 0) = 1
                    AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
                    AND NOT EXISTS (
                        SELECT 1
                        FROM `tabAOS User Block` b
                        WHERE b.status = 'Active'
                          AND (
                              (b.blocker_user = l.host_user AND b.blocked_user = v.`user`)
                              OR (b.blocked_user = l.host_user AND b.blocker_user = v.`user`)
                          )
                    )
                   THEN 1
                   ELSE 0
               END AS is_authorized
        FROM `tabAOS Live Stream View` v
        INNER JOIN `tabAOS Live Stream` l ON l.name = v.live_stream
        LEFT JOIN `tabUser` u ON u.name = v.`user`
        LEFT JOIN `tabAOS Profile` p ON p.user = v.`user`
        WHERE v.live_stream = %s AND v.is_active = 1
        ORDER BY v.creation ASC, v.name ASC
        LIMIT 2000
        """,
        (live_id,),
        as_dict=True,
    )
    present = set(identities)
    cutoff = add_to_date(now_datetime(), minutes=-2)
    closed = 0
    closed_watch_seconds = 0
    for row in rows:
        authorized = bool(row.is_authorized)
        identity_present = bool(
            row.livekit_identity and row.livekit_identity in present
        )
        if authorized and identity_present:
            continue
        last_seen = row.last_seen_at or row.joined_at
        if authorized and last_seen and last_seen > cutoff:
            continue
        view = frappe.get_doc("AOS Live Stream View", row.name)
        view.left_at = now_datetime()
        view.is_active = 0
        view.save(ignore_permissions=True)
        enqueue_view_removal(view.name)
        closed += 1
        closed_watch_seconds += max(0, int(view.watch_duration_seconds or 0))
    if closed:
        metrics = LiveAnalyticsService.handle_views_left(
            live_id=live_id,
            count=closed,
            watch_duration_seconds=closed_watch_seconds,
        ) or LiveAnalyticsService.get_view_metrics(live_id=live_id) or {}
        publish_viewer_count(live_id, max(0, int(metrics.get("viewer_count") or 0)))
    return closed


def _expire_pending_cohosts() -> int:
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live CoHost`
        WHERE status = 'pending' AND expires_at IS NOT NULL AND expires_at <= %s
        ORDER BY expires_at ASC, name ASC
        LIMIT %s
        """,
        (now_datetime(), BATCH_SIZE),
        as_dict=True,
    )
    count = 0
    for row in rows:
        doc = frappe.get_doc("AOS Live CoHost", row.name)
        if doc.status != "pending":
            continue
        doc.status = "expired"
        doc.is_active = 0
        doc.response_reason = "Co-host request expired."
        doc.save(ignore_permissions=True)
        count += 1
    return count


def _enqueue_reconcile_job(*, method: str, live_id: str, kind: str) -> bool:
    """Schedule one idempotent recovery unit with its own DB transaction."""
    try:
        frappe.enqueue(
            method,
            queue="short",
            enqueue_after_commit=True,
            live_id=live_id,
            job_id=f"aos:live:{kind}:{live_id}",
            deduplicate=True,
        )
        return True
    except Exception:
        live_log("reconciliation_enqueue", outcome="failure", reason=kind)
        return False


def _end_unavailable_host_live(live_id: str, *, host_user: str | None = None) -> bool:
    candidate_host = str(
        host_user or frappe.db.get_value("AOS Live Stream", live_id, "host_user") or ""
    ).strip()
    if not candidate_host:
        return False

    # The healthy path performs no row lock. Only an unavailable host enters
    # the mutation path, where the account policy is rechecked after locking.
    try:
        LivePolicy().require_account_available(candidate_host)
        return False
    except LiveError:
        pass

    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live Stream`
        WHERE name=%s AND status IN ('starting', 'live')
        LIMIT 1 FOR UPDATE
        """,
        (live_id,),
        as_dict=True,
    )
    if not rows:
        return False
    live = frappe.get_doc("AOS Live Stream", rows[0].name)
    try:
        LivePolicy().require_account_available(live.host_user)
        return False
    except LiveError:
        pass

    from aos.api.live.live import _close_cohost_workflows_for_live, _create_live_ended_message
    from aos.api.live.realtime import publish_live_ended

    was_live = live.status == STATUS_LIVE
    if was_live:
        _close_cohost_workflows_for_live(live=live, host_user=live.host_user)
    live.status = STATUS_ENDED if was_live else STATUS_FAILED
    live.room_cleanup_pending = 1
    live.last_room_error = "host_unavailable"
    live.save(ignore_permissions=True)
    live.reload()
    if was_live:
        _create_live_ended_message(live=live, host_user=live.host_user)
        publish_live_ended(live)
    try:
        frappe.enqueue(
            "aos.tasks.live.cleanup_live_room",
            queue="short",
            enqueue_after_commit=True,
            live_id=live.name,
        )
    except Exception:
        live_log("room_cleanup_enqueue", outcome="failure", reason="dependency")
    return True


def reconcile_active_live(*, live_id: str) -> dict[str, object]:
    """Reconcile one active room without holding DB locks across LiveKit I/O."""
    row = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["status", "is_active", "room_name", "host_user"],
        as_dict=True,
    )
    if (
        not row
        or row.status != STATUS_LIVE
        or not bool(row.is_active)
        or not row.room_name
    ):
        return {"ok": True, "outcome": "not_active", "viewers_closed": 0}

    if _end_unavailable_host_live(live_id, host_user=str(row.host_user or "")):
        return {"ok": True, "outcome": "host_ended", "viewers_closed": 0}

    participants = list_participants(row.room_name)
    if not participants.ok:
        live_log(
            "room_participant_reconcile",
            outcome="failure",
            reason=participants.category,
        )
        return {"ok": False, "outcome": participants.category, "viewers_closed": 0}

    # External I/O finished. Re-read canonical application state before any
    # mutation so an end that raced the participant listing always wins.
    current = frappe.db.get_value(
        "AOS Live Stream",
        live_id,
        ["status", "is_active", "room_name"],
        as_dict=True,
    )
    if (
        not current
        or current.status != STATUS_LIVE
        or not bool(current.is_active)
        or current.room_name != row.room_name
    ):
        return {"ok": True, "outcome": "state_changed", "viewers_closed": 0}

    if participants.category == "not_found":
        ensured = ensure_live_room(live_id=live_id)
        return {
            "ok": bool(ensured.get("ok")),
            "outcome": str(ensured.get("outcome") or "room_missing"),
            "viewers_closed": 0,
            "room_recreated": int(bool(ensured.get("ok"))),
        }

    closed = _close_missing_viewers(live_id, participants.participants)
    LiveAnalyticsService.materialize_view_metrics(live_id=live_id)
    LiveAnalyticsService.materialize_comment_count(live_id=live_id)
    LiveAnalyticsService.sync_reaction_count(live_id=live_id)
    frappe.db.set_value(
        "AOS Live Stream",
        live_id,
        "last_reconciled_at",
        now_datetime(),
        update_modified=False,
    )
    return {"ok": True, "outcome": "reconciled", "viewers_closed": closed}


def reconcile_live_state() -> dict[str, int]:
    """Dispatch bounded per-Live recovery jobs without external I/O in this transaction."""
    result = {
        "starting_checked": 0,
        "starting_scheduled": 0,
        "active_checked": 0,
        "active_scheduled": 0,
        "ended_checked": 0,
        "cleanup_scheduled": 0,
        "cohosts_expired": 0,
        "ended_views_finalized": 0,
        "failed": 0,
    }
    result["cohosts_expired"] = _expire_pending_cohosts()

    pending_terminal_views = frappe.db.sql(
        """
        SELECT DISTINCT v.live_stream AS name
        FROM `tabAOS Live Stream View` v
        INNER JOIN `tabAOS Live Stream` l ON l.name = v.live_stream
        WHERE v.is_active = 1 AND l.status = 'ended' AND l.is_active = 0
        ORDER BY v.live_stream ASC
        LIMIT 1
        """,
        as_dict=True,
    )
    for row in pending_terminal_views:
        try:
            outcome = finalize_ended_live_views(live_id=row.name)
            result["ended_views_finalized"] += int(outcome.get("closed") or 0)
        except Exception:
            result["failed"] += 1

    starting = frappe.get_all(
        "AOS Live Stream",
        filters={"status": STATUS_STARTING},
        fields=["name"],
        order_by="modified asc, name asc",
        limit=min(BATCH_SIZE, 25),
    )
    for row in starting:
        result["starting_checked"] += 1
        if _enqueue_reconcile_job(
            method="aos.tasks.live.activate_live_room",
            live_id=row.name,
            kind="activate",
        ):
            result["starting_scheduled"] += 1
        else:
            result["failed"] += 1

    ended = frappe.get_all(
        "AOS Live Stream",
        filters={"status": ["in", [STATUS_ENDED, STATUS_FAILED]], "room_cleanup_pending": 1},
        fields=["name"],
        order_by="modified asc, name asc",
        limit=BATCH_SIZE,
    )
    for row in ended:
        result["ended_checked"] += 1
        if _enqueue_reconcile_job(
            method="aos.tasks.live.cleanup_live_room",
            live_id=row.name,
            kind="cleanup",
        ):
            result["cleanup_scheduled"] += 1
        else:
            result["failed"] += 1

    active = frappe.get_all(
        "AOS Live Stream",
        filters={"status": "live", "is_active": 1},
        fields=["name", "room_name", "host_user"],
        order_by="last_reconciled_at asc, modified asc, name asc",
        limit=min(BATCH_SIZE, 25),
    )
    for row in active:
        result["active_checked"] += 1
        if _enqueue_reconcile_job(
            method="aos.tasks.live.reconcile_active_live",
            live_id=row.name,
            kind="active",
        ):
            result["active_scheduled"] += 1
        else:
            result["failed"] += 1

    live_log(
        "reconciliation",
        outcome="success" if not result["failed"] else "partial",
        reason="bounded_batch",
    )
    return result


def cleanup_live_webhook_events() -> int:
    cutoff = add_days(now_datetime(), -30)
    rows = frappe.get_all(
        "AOS LiveKit Webhook Event",
        filters={"creation": ["<", cutoff], "status": ["in", ["processed", "ignored"]]},
        pluck="name",
        order_by="creation asc, name asc",
        limit=500,
    )
    if rows:
        frappe.db.delete("AOS LiveKit Webhook Event", {"name": ["in", rows]})
    return len(rows)


def fanout_live_started_notifications(
    *,
    live_id: str,
    after_creation: str | None = None,
    after_name: str | None = None,
    delivered: int = 0,
) -> dict[str, object]:
    return deliver_live_started_fanout(
        live_id=live_id,
        after_creation=after_creation,
        after_name=after_name,
        delivered=delivered,
    )
