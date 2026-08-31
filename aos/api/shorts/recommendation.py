"""Public recommendation feedback APIs for Shorts."""

from __future__ import annotations

import time

import frappe

from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id, require_session_for_guest
from aos.api.shorts.tracking import _ensure_trackable_short
from aos.api.shorts.utils import resolve_actor
from aos.services.shorts.analytics import event_key
from aos.services.shorts.constants import (
    RECOMMENDATION_FEEDBACK_LIMIT_PER_MINUTE_PER_IP,
    VALID_RECOMMENDATION_FEEDBACK_ACTIONS,
)
from aos.services.shorts.recommendation import RecommendationService


def recommendation_feedback_impl(**kwargs):
    """Record explicit recommendation feedback without changing Short state.

    Supported feedback is deliberately negative/curation-oriented.  Positive
    feedback already has canonical domain actions (watch, like, save, share,
    repost, comment) and should not be duplicated through this endpoint.
    """
    rl = rate_limit(
        key=f"aos:shorts:recommendation_feedback:ip:{request_ip()}",
        ttl_seconds=60,
        limit=RECOMMENDATION_FEEDBACK_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    action = str(kwargs.get("action") or "").strip().lower()
    if action not in VALID_RECOMMENDATION_FEEDBACK_ACTIONS:
        return fail(
            "Invalid recommendation feedback action.",
            error="VALIDATION_ERROR",
            data={"field": "action"},
        )

    session_id, err = require_session_for_guest(kwargs.get("session_id"))
    if err:
        return err

    trackable_err = _ensure_trackable_short(short_id)
    if trackable_err:
        return trackable_err

    try:
        user, session_id = resolve_actor(session_id=session_id)
        actor = f"user:{user}" if user else f"session:{session_id}"
        dedupe_id = str(
            kwargs.get("event_id")
            or f"bucket:{int(time.time() // 10)}"
        )[:140]

        doc = frappe.get_doc(
            {
                "doctype": "AOS Short Event",
                "short": short_id,
                "user": user,
                "session_id": None if user else session_id,
                "event_type": action,
                "source": "recommendation_feedback",
                "metadata": {"action": action},
                "event_key": event_key(
                    event_type=action,
                    short_id=short_id,
                    actor_key=actor,
                    client_event_id=dedupe_id,
                ),
            }
        )
        created = True
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if is_duplicate_entry_error(exc):
                created = False
            else:
                raise

        RecommendationService.invalidate_profile(user=user, session_id=session_id)

        return ok(
            "Recommendation preference updated.",
            data={
                "short_id": short_id,
                "action": action,
                "recorded": created,
            },
        )
    except frappe.ValidationError as exc:
        return fail(str(exc), error="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Shorts Recommendation Feedback Failed",
        )
        return fail(
            "Failed to update recommendation preference.",
            error="INTERNAL_ERROR",
        )
