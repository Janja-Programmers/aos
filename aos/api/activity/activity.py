"""Private Activity Center API implementation."""

from __future__ import annotations

import uuid

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.services.accounts.http import set_private_no_store
from aos.services.activity.constants import (
    ACTIVE_STATUS,
    ACTIVITY_DOCTYPE,
    CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
    CLEAR_FIELDS,
    HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
    HIDE_FIELDS,
    LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
    LIST_FIELDS,
)
from aos.services.activity.errors import ActivityError
from aos.services.activity.observability import activity_log
from aos.services.activity.validation import (
    ensure_known_fields,
    normalize_activity_id,
    normalize_group_filter,
    normalize_limit,
    normalize_start,
    normalize_type_filter,
)
from aos.services.activity_service import ActivityService


_PUBLIC_ACTIVITY_ERROR_MESSAGES = {
    "VALIDATION_ERROR": "Invalid activity request.",
    "NOT_FOUND": "Activity not found.",
}


def _domain_error(exc: ActivityError):
    code = str(getattr(exc, "code", "VALIDATION_ERROR") or "VALIDATION_ERROR").strip().upper()
    message = _PUBLIC_ACTIVITY_ERROR_MESSAGES.get(code, "Invalid activity request.")
    return fail(message, error=code, http_status=exc.http_status)


def _rollback_savepoint(savepoint: str) -> None:
    try:
        frappe.db.rollback(save_point=savepoint)
    except Exception:
        # Never roll back the caller's full transaction from Activity Center.
        pass


def list_activity_impl(**kwargs):
    """List only the current user's active private Activity Center rows."""
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:activity:list:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        ensure_known_fields(kwargs, LIST_FIELDS)
        limit = normalize_limit(kwargs.get("limit"))
        start = normalize_start(kwargs.get("start"))
        group = normalize_group_filter(kwargs)
        activity_type = normalize_type_filter(kwargs)

        filters = {"user": current_user, "status": ACTIVE_STATUS}
        if group:
            filters["activity_group"] = group
        if activity_type:
            filters["activity_type"] = activity_type

        fields = [
            "name",
            "activity_group",
            "activity_type",
            "status",
            "target_doctype",
            "target_name",
            "target_title",
            "target_subtitle",
            "target_image",
            "route_type",
            "route_id",
            "metadata_json",
            "occurred_at",
            "last_occurrence_at",
            "count",
        ]
        total = int(frappe.db.count(ACTIVITY_DOCTYPE, filters=filters) or 0)
        rows = frappe.get_all(
            ACTIVITY_DOCTYPE,
            filters=filters,
            fields=fields,
            order_by="last_occurrence_at desc, creation desc, name desc",
            offset=start,
            limit=limit,
        )
        items = [ActivityService.serialize_activity(row) for row in rows]
        activity_log("activity.listed", count=len(items), activity_group=group, activity_type=activity_type)
        return ok(
            "Activity fetched.",
            data={
                "items": items,
                "total": total,
                "limit": limit,
                "start": start,
                "has_more": (start + len(items)) < total,
                "group": group or None,
                "type": activity_type or None,
            },
        )
    except ActivityError as exc:
        activity_log("activity.listed", outcome="rejected")
        return _domain_error(exc)
    except Exception:
        activity_log("activity.listed", outcome="failure")
        frappe.log_error("Activity list operation failed.", "AOS List Activity Failed")
        return fail("Failed to fetch activity.", error="INTERNAL_ERROR")


def hide_activity_impl(**kwargs):
    """Hide one current-user Activity Center item idempotently."""
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:activity:hide:user:{current_user}",
        ttl_seconds=60,
        limit=HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        ensure_known_fields(kwargs, HIDE_FIELDS)
        activity_id = normalize_activity_id(kwargs)
    except ActivityError as exc:
        activity_log("activity.hidden", outcome="rejected")
        return _domain_error(exc)

    savepoint = f"aos_activity_hide_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        hidden = ActivityService.hide_activity(user=current_user, activity_id=activity_id)
        if not hidden:
            _rollback_savepoint(savepoint)
            activity_log("activity.hidden", outcome="not_found")
            return fail("Activity not found.", error="NOT_FOUND")
        activity_log("activity.hidden", activity_id=activity_id)
        return ok("Activity hidden.", data={"id": activity_id})
    except Exception:
        _rollback_savepoint(savepoint)
        activity_log("activity.hidden", outcome="failure")
        frappe.log_error("Activity hide operation failed.", "AOS Hide Activity Failed")
        return fail("Failed to hide activity.", error="INTERNAL_ERROR")


def clear_activity_impl(**kwargs):
    """Clear current-user Activity Center items, optionally by group/type."""
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:activity:clear:user:{current_user}",
        ttl_seconds=60,
        limit=CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        ensure_known_fields(kwargs, CLEAR_FIELDS)
        group = normalize_group_filter(kwargs)
        activity_type = normalize_type_filter(kwargs)
    except ActivityError as exc:
        activity_log("activity.cleared", outcome="rejected")
        return _domain_error(exc)

    savepoint = f"aos_activity_clear_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        cleared_count = ActivityService.clear_activity(
            user=current_user,
            activity_group=group or None,
            activity_type=activity_type or None,
        )
        activity_log(
            "activity.cleared",
            count=cleared_count,
            activity_group=group,
            activity_type=activity_type,
        )
        return ok(
            "Activity cleared.",
            data={
                "cleared_count": cleared_count,
                "group": group or None,
                "type": activity_type or None,
            },
        )
    except Exception:
        _rollback_savepoint(savepoint)
        activity_log("activity.cleared", outcome="failure")
        frappe.log_error("Activity clear operation failed.", "AOS Clear Activity Failed")
        return fail("Failed to clear activity.", error="INTERNAL_ERROR")
