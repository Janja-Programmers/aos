"""
Tracking APIs for Shorts.

Handles:
- impressions (event logging)
- views (dedup + qualification)
- shares (event + counter)
"""

from __future__ import annotations

import time

import frappe
from aos.api.shared.auth import current_user
from frappe.utils import now_datetime, getdate

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.formatters import humanize_count
from aos.api.shared.validators import (
    require_id,
    require_session_for_guest,
    normalize_watch_ms,
)

from aos.api.shorts.constants import (
    TRACK_VIEW_LIMIT_PER_MINUTE_PER_IP,
    TRACK_IMPRESSION_LIMIT_PER_MINUTE_PER_IP,
    TRACK_SHARE_LIMIT_PER_MINUTE_PER_IP,
)

from aos.api.shorts.utils import resolve_actor
from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.activity import record_short_watch_activity
from aos.services.analytics_pipeline_service import emit_analytics_event
from aos.services.shorts.analytics import bounded_watch_ms, event_key
from aos.services.shorts.repository import ShortsRepository


# COMMON
def _ensure_trackable_short(short_id: str, *, viewer: str | None = None):
    """
    Ensure the short exists and is trackable by this viewer.

    Tracking should only apply to ready + visible shorts that the current
    viewer is allowed to view. This prevents private/restricted shorts from
    receiving leaked analytics events from guessed IDs.
    """
    short = frappe.db.get_value(
        "AOS Short",
        short_id,
        ["name", "owner", "status", "visibility_status", "audience"],
        as_dict=True,
    )

    if not short:
        return fail("Short not found.", error="NOT_FOUND")

    if short.status != "ready" or short.visibility_status != "visible":
        return fail(
            "Short is not available for tracking.",
            error="VALIDATION_ERROR",
        )

    viewer = viewer or current_user()
    if viewer == "Guest":
        viewer = None

    if not can_view_short(short, current_user=viewer):
        return fail("Short not found.", error="NOT_FOUND")

    return None


def _short_view_identity_key(*, user: str | None, session_id: str | None) -> str | None:
    if user:
        return f"user:{user}"
    if session_id:
        return f"session:{session_id}"
    return None


def _insert_event_once(
    *, short_id: str, event_type: str, user: str | None, session_id: str | None,
    client_event_id: object | None, source: str | None = None, metadata: object | None = None,
    bucket_seconds: int = 10,
) -> bool:
    actor = f"user:{user}" if user else f"session:{session_id}"
    dedupe_id = str(client_event_id or f"bucket:{int(time.time() // max(1, bucket_seconds))}")[:140]
    doc = frappe.get_doc({
        "doctype": "AOS Short Event",
        "short": short_id,
        "user": user,
        "session_id": None if user else session_id,
        "event_type": event_type,
        "source": source,
        "metadata": metadata or {},
        "event_key": event_key(
            event_type=event_type, short_id=short_id, actor_key=actor, client_event_id=dedupe_id
        ),
    })
    try:
        doc.insert(ignore_permissions=True)
        return True
    except Exception as exc:
        if is_duplicate_entry_error(exc):
            return False
        raise


# TRACK IMPRESSION
def track_impression_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:impression:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_IMPRESSION_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    session_id, err = require_session_for_guest(kwargs.get("session_id"))
    if err:
        return err

    trackable_err = _ensure_trackable_short(short_id)
    if trackable_err:
        return trackable_err

    try:
        user, session_id = resolve_actor(session_id=session_id)

        created = _insert_event_once(
            short_id=short_id, event_type="impression", user=user, session_id=session_id,
            client_event_id=kwargs.get("event_id"), bucket_seconds=10,
        )
        if created:
            ShortsRepository().increment_counter(short_id, "impression_count", 1)

        if created:
            try:
                emit_analytics_event(
                    event_type="short_impression",
                    event_group="shorts",
                    user=user,
                    session_id=session_id,
                    target_doctype="AOS Short",
                    target_name=short_id,
                    route_type="short",
                    route_id=short_id,
                    source="shorts.track_impression",
                )
            except Exception:
                frappe.log_error("Shorts operation failed.", "short impression analytics emit failed")

        return ok(
            "Impression tracked.",
            data={"short_id": short_id},
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "track_impression failed")
        return fail("Failed to track impression", error="INTERNAL_ERROR")


# TRACK VIEW
def track_view_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:view:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_VIEW_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    watch_ms, err = normalize_watch_ms(kwargs.get("watch_ms"))
    if err:
        return err

    session_id, err = require_session_for_guest(kwargs.get("session_id"))
    if err:
        return err

    trackable_err = _ensure_trackable_short(short_id)
    if trackable_err:
        return trackable_err

    try:
        user, session_id = resolve_actor(session_id=session_id)
        duration_seconds = frappe.db.get_value("AOS Short", short_id, "duration_seconds") or 0
        watch_ms = bounded_watch_ms(watch_ms, duration_seconds=duration_seconds)

        today = getdate()

        identity_key = _short_view_identity_key(
            user=user,
            session_id=session_id,
        )

        filters = {
            "short": short_id,
            "view_date": today,
            "identity_key": identity_key,
        }

        if user:
            filters["user"] = user
        else:
            filters["session_id"] = session_id

        name = frappe.db.get_value("AOS Short View", filters, "name")

        should_update_ranking = False

        # CREATE NEW VIEW
        if not name:
            doc = frappe.get_doc(
                {
                    "doctype": "AOS Short View",
                    "short": short_id,
                    "user": user,
                    "session_id": session_id,
                    "view_date": today,
                    "identity_key": identity_key,
                    "watch_ms": watch_ms,
                    "last_seen_at": now_datetime(),
                }
            )
            doc.insert(ignore_permissions=True)

            should_update_ranking = True

        # UPDATE EXISTING VIEW
        else:
            doc = frappe.get_doc("AOS Short View", name)

            new_watch_ms = max(doc.watch_ms or 0, watch_ms)

            if new_watch_ms != doc.watch_ms:
                doc.watch_ms = new_watch_ms
                doc.last_seen_at = now_datetime()
                doc.save(ignore_permissions=True)

                should_update_ranking = True

        # Record private Activity Center watch history for logged-in users only.
        # Guests still contribute to analytics/views but do not get account history.
        # Activity Center is secondary; a concurrency error there must not fail
        # the actual view tracking request.
        if user:
            try:
                record_short_watch_activity(
                    user=user,
                    short_id=short_id,
                    watch_ms=watch_ms,
                )
            except Exception:
                frappe.log_error(
                    "Shorts operation failed.",
                    "record_short_watch_activity failed",
                )


        if should_update_ranking:
            try:
                emit_analytics_event(
                    event_type="short_view",
                    event_group="shorts",
                    user=user,
                    session_id=session_id,
                    target_doctype="AOS Short",
                    target_name=short_id,
                    route_type="short",
                    route_id=short_id,
                    source="shorts.track_view",
                    metrics={"watch_ms": watch_ms},
                    metadata={"qualified_candidate": True},
                )
            except Exception:
                frappe.log_error("Shorts operation failed.", "short view analytics emit failed")

        # TRIGGER RANKING (ASYNC)
        if should_update_ranking:
            frappe.enqueue(
                "aos.api.shorts.tasks.update_short_score_task",
                short_id=short_id,
                queue="short",
                enqueue_after_commit=True,
            )

        return ok(
            "View tracked.",
            data={"short_id": short_id},
        )

    except Exception as ex:

        if is_duplicate_entry_error(ex):
            try:
                existing_name = frappe.db.get_value(
                    "AOS Short View",
                    filters,
                    "name",
                )

                if existing_name:
                    doc = frappe.get_doc("AOS Short View", existing_name)
                    new_watch_ms = max(doc.watch_ms or 0, watch_ms)

                    if new_watch_ms != doc.watch_ms:
                        doc.watch_ms = new_watch_ms
                        doc.last_seen_at = now_datetime()
                        doc.save(ignore_permissions=True)


                    return ok(
                        "View tracked.",
                        data={"short_id": short_id},
                    )

            except Exception:
                frappe.log_error(
                    "Shorts operation failed.",
                    "track_view duplicate recovery failed",
                )

        frappe.log_error("Shorts operation failed.", "track_view failed")
        return fail("Failed to track view", error="INTERNAL_ERROR")


# TRACK SHARE
def track_share_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:share:ip:{request_ip()}",
        ttl_seconds=60,
        limit=TRACK_SHARE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    session_id, err = require_session_for_guest(kwargs.get("session_id"))
    if err:
        return err

    trackable_err = _ensure_trackable_short(short_id)
    if trackable_err:
        return trackable_err

    try:
        user, session_id = resolve_actor(session_id=session_id)
        channel = str(kwargs.get("channel") or kwargs.get("source") or "").strip()

        created = _insert_event_once(
            short_id=short_id, event_type="share", user=user, session_id=session_id,
            client_event_id=kwargs.get("event_id"), source=channel,
            metadata={"channel": channel} if channel else {}, bucket_seconds=30,
        )
        if created:
            ShortsRepository().increment_counter(short_id, "share_count", 1)

        if created:
            try:
                emit_analytics_event(
                    event_type="short_share",
                    event_group="shorts",
                    user=user,
                    session_id=session_id,
                    target_doctype="AOS Short",
                    target_name=short_id,
                    route_type="short",
                    route_id=short_id,
                    source="shorts.track_share",
                    metadata={"channel": channel},
                )
            except Exception:
                frappe.log_error("Shorts operation failed.", "short share analytics emit failed")

        share_count = frappe.db.get_value("AOS Short", short_id, "share_count") or 0

        if created:
            frappe.enqueue(
                "aos.api.shorts.tasks.update_short_score_task",
                short_id=short_id,
                queue="short",
                enqueue_after_commit=True,
            )

        return ok(
            "Share tracked.",
            data={
                "short_id": short_id,
                "metrics": {
                    "share_count": int(share_count),
                    "share_count_display": humanize_count(share_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Shorts operation failed.", "track_share failed")
        return fail("Failed to track share", error="INTERNAL_ERROR")
