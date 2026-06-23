"""
Tracking APIs for Shorts.

Handles:
- impressions (event logging)
- views (dedup + qualification)
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime, getdate

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import (
    require_id,
    require_session_for_guest,
    normalize_watch_ms,
)

from aos.api.shorts.constants import (
    TRACK_VIEW_LIMIT_PER_MINUTE_PER_IP,
    TRACK_IMPRESSION_LIMIT_PER_MINUTE_PER_IP,
)

from aos.api.shorts.utils import resolve_actor
from aos.api.shorts.activity import record_short_watch_activity


# COMMON
def _ensure_trackable_short(short_id: str):
    """
    Ensure the short exists and is publicly trackable.

    Tracking should only apply to ready + visible shorts.
    This prevents hidden, failed, processing, or deleted shorts from polluting
    analytics/ranking data.
    """
    short = frappe.db.get_value(
        "AOS Short",
        short_id,
        ["name", "status", "visibility_status"],
        as_dict=True,
    )

    if not short:
        return fail("Short not found.", code="NOT_FOUND")

    if short.status != "ready" or short.visibility_status != "visible":
        return fail(
            "Short is not available for tracking.",
            code="VALIDATION_ERROR",
        )

    return None


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

        frappe.get_doc(
            {
                "doctype": "AOS Short Event",
                "short": short_id,
                "user": user,
                "session_id": session_id,
                "event_type": "impression",
            }
        ).insert(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Impression tracked.",
            data={"short_id": short_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "track_impression failed")
        frappe.db.rollback()
        return fail("Failed to track impression", code="INTERNAL_ERROR")


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

        today = getdate()

        filters = {
            "short": short_id,
            "view_date": today,
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
        if user:
            record_short_watch_activity(
                user=user,
                short_id=short_id,
                watch_ms=watch_ms,
            )

        frappe.db.commit()

        # TRIGGER RANKING (ASYNC)
        if should_update_ranking:
            frappe.enqueue(
                "aos.api.shorts.tasks.update_short_score_task",
                short_id=short_id,
                queue="short",
            )

        return ok(
            "View tracked.",
            data={"short_id": short_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "track_view failed")
        frappe.db.rollback()
        return fail("Failed to track view", code="INTERNAL_ERROR")
