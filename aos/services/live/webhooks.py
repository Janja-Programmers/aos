"""Verified, replay-safe LiveKit webhook processing for Live rooms."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import frappe
from frappe.utils import get_datetime, now_datetime
from livekit import api

from aos.api.shared.db import is_duplicate_entry_error
from aos.services.live_analytics_service import LiveAnalyticsService
from aos.services.livekit_service import LiveKitService

from .api import _outbox_flag, _rollback, _snapshot_callbacks
from .constants import LIVE_WEBHOOK_EVENT_DOCTYPE
from .errors import LiveError
from .participants import enqueue_view_removal
from .policy import LivePolicy
from .observability import live_log
from .repository import LiveRepository

MAX_WEBHOOK_BYTES = 131_072
SUPPORTED_EVENTS = frozenset(
    {
        "room_started",
        "room_finished",
        "participant_joined",
        "participant_left",
        "participant_connection_aborted",
    }
)


def _event_timestamp(value: Any):
    try:
        epoch = int(value or 0)
    except (TypeError, ValueError):
        return None
    if epoch <= 0:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).replace(tzinfo=None)


def verify_webhook(raw_body: str, authorization: str):
    if not raw_body or len(raw_body.encode("utf-8")) > MAX_WEBHOOK_BYTES:
        raise ValueError("invalid_body")
    if not authorization:
        raise ValueError("missing_authorization")
    api_key, api_secret = LiveKitService._get_credentials()
    receiver = api.WebhookReceiver(api.TokenVerifier(api_key, api_secret))
    return receiver.receive(raw_body, authorization)


def _live_for_room(room_name: str, *, lock: bool = False):
    if not room_name:
        return None
    if lock:
        rows = frappe.db.sql(
            """
            SELECT name
            FROM `tabAOS Live Stream`
            WHERE room_name = %s
            LIMIT 1 FOR UPDATE
            """,
            (room_name,),
            as_dict=True,
        )
        if not rows:
            return None
        return frappe.get_doc("AOS Live Stream", rows[0].name)
    live_id = frappe.db.get_value("AOS Live Stream", {"room_name": room_name}, "name")
    return frappe.get_doc("AOS Live Stream", live_id) if live_id else None


def _is_stale_for_live(live, event_created_at) -> bool:
    if not event_created_at or not live.started_at:
        return False
    return get_datetime(event_created_at) < get_datetime(live.started_at)


def _finish_room(room_name: str, event_created_at) -> str:
    live = _live_for_room(room_name, lock=True)
    if not live:
        return "unknown_room"
    if _is_stale_for_live(live, event_created_at):
        return "stale_event"
    if live.status == "ended" or not bool(live.is_active):
        if bool(getattr(live, "room_cleanup_pending", 0)):
            frappe.db.set_value(
                "AOS Live Stream",
                live.name,
                "room_cleanup_pending",
                0,
                update_modified=False,
            )
        return "already_ended"

    # Import lazily to avoid a service/API module cycle during app startup.
    from aos.api.live.live import (
        _close_cohost_workflows_for_live,
        _create_live_ended_message,
    )
    from aos.api.live.realtime import publish_live_ended

    _close_cohost_workflows_for_live(live=live, host_user=live.host_user)
    live.status = "ended"
    live.room_cleanup_pending = 0
    live.save(ignore_permissions=True)
    live.reload()
    _create_live_ended_message(live=live, host_user=live.host_user)
    publish_live_ended(live)
    return "live_ended"


def _touch_participant(room_name: str, identity: str) -> str:
    if not room_name or not identity:
        return "missing_participant"
    live = _live_for_room(room_name)
    if not live or live.status != "live" or not bool(live.is_active):
        return "inactive_room"
    rows = frappe.db.sql(
        """
        SELECT name, `user`
        FROM `tabAOS Live Stream View`
        WHERE live_stream = %s AND livekit_identity = %s AND is_active = 1
        ORDER BY creation DESC, name DESC
        LIMIT 1 FOR UPDATE
        """,
        (live.name, identity),
        as_dict=True,
    )
    if not rows:
        return "untracked_participant"
    row = rows[0]
    try:
        policy = LivePolicy()
        viewer = str(row.user) if row.user else None
        policy.lock_relationship(host_user=live.host_user, viewer=viewer)
        policy.require_view_access(
            host_user=live.host_user,
            viewer=viewer,
        )
    except LiveError:
        view = frappe.get_doc("AOS Live Stream View", row.name)
        view.left_at = now_datetime()
        view.is_active = 0
        view.save(ignore_permissions=True)
        LiveAnalyticsService.handle_view_left(
            live_id=live.name,
            watch_duration_seconds=int(view.watch_duration_seconds or 0),
        )
        enqueue_view_removal(row.name)
        return "participant_denied"
    frappe.db.set_value(
        "AOS Live Stream View",
        row.name,
        "last_seen_at",
        now_datetime(),
        update_modified=False,
    )
    return "participant_seen"


def _leave_participant(room_name: str, identity: str) -> str:
    if not room_name or not identity:
        return "missing_participant"
    live = _live_for_room(room_name)
    if not live:
        return "unknown_room"
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Live Stream View`
        WHERE live_stream = %s AND livekit_identity = %s AND is_active = 1
        ORDER BY creation DESC, name DESC
        LIMIT 1 FOR UPDATE
        """,
        (live.name, identity),
        as_dict=True,
    )
    if not rows:
        return "already_left"
    view = frappe.get_doc("AOS Live Stream View", rows[0].name)
    view.left_at = now_datetime()
    view.is_active = 0
    view.save(ignore_permissions=True)
    metrics = LiveAnalyticsService.handle_view_left(
        live_id=live.name,
        watch_duration_seconds=int(view.watch_duration_seconds or 0),
    ) or LiveAnalyticsService.get_view_metrics(live_id=live.name) or {}
    from aos.api.live.realtime import publish_viewer_count

    publish_viewer_count(live.name, max(0, int(metrics.get("viewer_count") or 0)))
    return "participant_left"


def _process(event) -> tuple[str, str]:
    event_type = str(getattr(event, "event", "") or "").strip().lower()
    room = getattr(event, "room", None)
    participant = getattr(event, "participant", None)
    room_name = str(getattr(room, "name", "") or "").strip()
    identity = str(getattr(participant, "identity", "") or "").strip()
    event_created_at = _event_timestamp(getattr(event, "created_at", None))
    if event_type not in SUPPORTED_EVENTS:
        return "ignored", "unsupported_event"
    if event_type == "room_finished":
        return "processed", _finish_room(room_name, event_created_at)
    if event_type == "participant_joined":
        return "processed", _touch_participant(room_name, identity)
    if event_type in {"participant_left", "participant_connection_aborted"}:
        return "processed", _leave_participant(room_name, identity)
    # room_started is informational only. It must never revive application state.
    return "processed", "room_observed"


def handle_verified_webhook(raw_body: str, authorization: str) -> dict[str, Any]:
    try:
        event = verify_webhook(raw_body, authorization)
    except Exception:
        live_log("livekit_webhook", outcome="rejected", reason="signature")
        return {"ok": False, "error": "LIVE_WEBHOOK_INVALID"}

    event_id = str(getattr(event, "id", "") or "").strip()
    event_type = str(getattr(event, "event", "") or "").strip().lower()
    if not event_id or len(event_id) > 140 or not event_type:
        live_log("livekit_webhook", outcome="rejected", reason="schema")
        return {"ok": False, "error": "LIVE_WEBHOOK_INVALID"}

    payload_hash = hashlib.sha256(raw_body.encode("utf-8")).hexdigest()
    savepoint = f"aos_live_webhook_{uuid.uuid4().hex[:12]}"
    callbacks = _snapshot_callbacks()
    outbox_flag = _outbox_flag()
    frappe.db.savepoint(savepoint)

    doc = frappe.new_doc(LIVE_WEBHOOK_EVENT_DOCTYPE)
    doc.event_id = event_id
    doc.event_type = event_type
    doc.status = "processing"
    doc.event_created_at = _event_timestamp(getattr(event, "created_at", None))
    doc.payload_hash = payload_hash
    try:
        doc.insert(ignore_permissions=True)
    except Exception as exc:
        _rollback(savepoint, callbacks, outbox_flag)
        if not is_duplicate_entry_error(exc):
            live_log("livekit_webhook", outcome="failure", reason="dependency")
            return {"ok": False, "error": "LIVE_WEBHOOK_RETRY"}

        existing = frappe.db.get_value(
            LIVE_WEBHOOK_EVENT_DOCTYPE,
            {"event_id": event_id},
            ["status", "payload_hash"],
            as_dict=True,
        )
        if not existing:
            live_log("livekit_webhook", outcome="failure", reason="dependency")
            return {"ok": False, "error": "LIVE_WEBHOOK_RETRY"}
        if str(existing.payload_hash or "") != payload_hash:
            live_log("livekit_webhook", outcome="rejected", reason="signature")
            return {"ok": False, "error": "LIVE_WEBHOOK_INVALID"}
        if str(existing.status or "") == "processing":
            live_log("livekit_webhook", outcome="conflict", reason="replay")
            return {"ok": False, "error": "LIVE_WEBHOOK_RETRY"}
        live_log("livekit_webhook", outcome="duplicate", reason="replay")
        return {"ok": True, "duplicate": True, "outcome": "already_processed"}

    try:
        status, outcome = _process(event)
        doc.status = status
        doc.outcome = outcome[:140]
        doc.processed_at = now_datetime()
        doc.save(ignore_permissions=True)
        live_log("livekit_webhook", outcome="success", reason=outcome)
        return {"ok": True, "duplicate": False, "outcome": outcome}
    except Exception:
        # Roll back the dedupe insert, application mutations, after-commit
        # callbacks and outbox-registration flag together. A non-2xx response
        # then allows LiveKit to retry the complete event safely.
        _rollback(savepoint, callbacks, outbox_flag)
        live_log("livekit_webhook", outcome="failure", reason="processing")
        frappe.log_error("LiveKit webhook processing failed.", "LiveKit webhook failure")
        return {"ok": False, "error": "LIVE_WEBHOOK_RETRY"}
