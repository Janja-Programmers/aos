"""
Engagement APIs for Shorts.

Handles:
- like / unlike (toggle)
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.services.notification_service import NotificationService

from aos.api.shorts.constants import (
    LIKE_TOGGLE_RATE_LIMIT_PER_MINUTE,
)
from aos.api.shorts.activity import (
    hide_short_like_activity,
    record_short_like_activity,
)


# TOGGLE LIKE
def toggle_like_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:like:user:{user}",
        ttl_seconds=60,
        limit=LIKE_TOGGLE_RATE_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        should_update_ranking = False

        # FETCH SHORT
        # owner is the creator/poster user.
        # seller is optional shop context and should not be used as notification recipient.
        short = frappe.db.get_value(
            "AOS Short",
            short_id,
            [
                "name",
                "owner",
                "status",
                "visibility_status",
            ],
            as_dict=True,
        )

        if not short:
            return fail("Short not found.", code="NOT_FOUND")

        if short.status != "ready" or short.visibility_status != "visible":
            return fail(
                "Short is not available for likes.",
                code="VALIDATION_ERROR",
            )

        # CHECK EXISTING LIKE
        existing = frappe.get_all(
            "AOS Short Like",
            filters={"short": short_id, "user": user},
            fields=["name"],
            limit=1,
        )

        # LIKE
        if not existing:
            frappe.get_doc(
                {
                    "doctype": "AOS Short Like",
                    "short": short_id,
                    "user": user,
                }
            ).insert(ignore_permissions=True)

            liked = True
            message = "Liked."
            should_update_ranking = True

            record_short_like_activity(
                user=user,
                short_id=short_id,
            )

            # NOTIFICATION
            if short.owner and short.owner != user:
                NotificationService.notify_short_like(
                    user=short.owner,
                    actor=user,
                    short_id=short_id,
                )

        # UNLIKE
        else:
            frappe.delete_doc(
                "AOS Short Like",
                existing[0].name,
                ignore_permissions=True,
            )

            liked = False
            message = "Unliked."
            should_update_ranking = True

            hide_short_like_activity(
                user=user,
                short_id=short_id,
            )

        frappe.db.commit()

        # Read canonical count after AOS Short Like hooks update the metric.
        like_count = frappe.db.get_value("AOS Short", short_id, "like_count") or 0

        # TRIGGER RANKING (ASYNC)
        if should_update_ranking:
            frappe.enqueue(
                "aos.api.shorts.tasks.update_short_score_task",
                short_id=short_id,
                queue="short",
            )

        return ok(
            message,
            data={
                "short_id": short_id,

                # Backward-compatible field.
                # Frontend should eventually prefer viewer_state.is_liked.
                "liked": liked,

                "viewer_state": {
                    "is_liked": liked,
                },
                "metrics": {
                    "like_count": int(like_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "toggle_like failed")
        frappe.db.rollback()
        return fail("Failed to toggle like", code="INTERNAL_ERROR")
