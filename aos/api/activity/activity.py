"""Private Activity Center API implementation."""

from __future__ import annotations

import uuid

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
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
from aos.services.activity.cursor import decode_cursor, encode_cursor
from aos.services.activity.errors import ActivityError
from aos.services.activity.observability import activity_log
from aos.services.activity.projection import project_activity_rows
from aos.services.activity.validation import (
    ensure_known_fields,
    normalize_activity_id,
    normalize_cursor,
    normalize_group_filter,
    normalize_limit,
    normalize_type_filter,
    validate_filter_pair,
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
        pass


def _timeline_rows(
    *,
    user: str,
    group: str,
    activity_type: str,
    cursor: dict | None,
    limit: int,
):
    conditions = ["user = %(user)s", "status = %(status)s"]
    values: dict[str, object] = {"user": user, "status": ACTIVE_STATUS, "limit": limit + 1}
    if group:
        conditions.append("activity_group = %(group)s")
        values["group"] = group
    if activity_type:
        conditions.append("activity_type = %(activity_type)s")
        values["activity_type"] = activity_type
    if cursor:
        values.update(
            {
                "cursor_last": cursor["last_occurrence_at"],
                "cursor_creation": cursor["creation"],
                "cursor_id": cursor["public_id"],
            }
        )
        conditions.append(
            """(
                last_occurrence_at < %(cursor_last)s
                OR (last_occurrence_at = %(cursor_last)s AND creation < %(cursor_creation)s)
                OR (last_occurrence_at = %(cursor_last)s AND creation = %(cursor_creation)s AND public_id < %(cursor_id)s)
            )"""
        )
    return frappe.db.sql(
        f"""
        SELECT public_id, activity_group, activity_type, status,
               target_doctype, target_name, target_title, target_subtitle,
               target_image, route_type, route_id, metadata_json,
               occurred_at, last_occurrence_at, `count`, creation
        FROM `tab{ACTIVITY_DOCTYPE}`
        WHERE {' AND '.join(conditions)}
        ORDER BY last_occurrence_at DESC, creation DESC, public_id DESC
        LIMIT %(limit)s
        """,
        values,
        as_dict=True,
    )


def list_activity_impl(**kwargs):
    """List the current user's private Activity Center using keyset pagination."""
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("activity", "list", "user", current_user),
        ttl_seconds=60,
        limit=LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    try:
        ensure_known_fields(kwargs, LIST_FIELDS)
        limit = normalize_limit(kwargs.get("limit"))
        group = normalize_group_filter(kwargs)
        activity_type = normalize_type_filter(kwargs)
        validate_filter_pair(group=group, activity_type=activity_type)
        cursor_token = normalize_cursor(kwargs.get("cursor"))
        cursor = decode_cursor(
            cursor_token,
            user=current_user,
            group=group,
            activity_type=activity_type,
        )
        rows = _timeline_rows(
            user=current_user,
            group=group,
            activity_type=activity_type,
            cursor=cursor,
            limit=limit,
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        projected = project_activity_rows(page_rows, viewer=current_user)
        items = [ActivityService.serialize_activity(row) for row in projected]
        next_cursor = None
        if has_more and page_rows:
            next_cursor = encode_cursor(
                user=current_user,
                group=group,
                activity_type=activity_type,
                row=page_rows[-1],
            )
        activity_log("activity.listed", count=len(items), activity_group=group, activity_type=activity_type)
        return ok(
            "Activity fetched.",
            data={
                "items": items,
                "limit": limit,
                "has_more": has_more,
                "next_cursor": next_cursor,
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
    limited = rate_limit(
        key=rate_limit_key("activity", "hide", "user", current_user),
        ttl_seconds=60,
        limit=HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited
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
            return fail("Activity not found.", error="NOT_FOUND", http_status=404)
        activity_log("activity.hidden", activity_id=activity_id)
        return ok("Activity hidden.", data={"id": activity_id})
    except Exception:
        _rollback_savepoint(savepoint)
        activity_log("activity.hidden", outcome="failure")
        frappe.log_error("Activity hide operation failed.", "AOS Hide Activity Failed")
        return fail("Failed to hide activity.", error="INTERNAL_ERROR")


def clear_activity_impl(**kwargs):
    """Clear one bounded batch of the current user's Activity Center."""
    set_private_no_store()
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("activity", "clear", "user", current_user),
        ttl_seconds=60,
        limit=CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited
    try:
        ensure_known_fields(kwargs, CLEAR_FIELDS)
        group = normalize_group_filter(kwargs)
        activity_type = normalize_type_filter(kwargs)
        validate_filter_pair(group=group, activity_type=activity_type)
    except ActivityError as exc:
        activity_log("activity.cleared", outcome="rejected")
        return _domain_error(exc)

    savepoint = f"aos_activity_clear_{uuid.uuid4().hex[:12]}"
    frappe.db.savepoint(savepoint)
    try:
        cleared_count, has_more = ActivityService.clear_activity(
            user=current_user,
            activity_group=group or None,
            activity_type=activity_type or None,
        )
        activity_log("activity.cleared", count=cleared_count, activity_group=group, activity_type=activity_type)
        return ok(
            "Activity cleared.",
            data={
                "cleared_count": cleared_count,
                "has_more": has_more,
                "group": group or None,
                "type": activity_type or None,
            },
        )
    except Exception:
        _rollback_savepoint(savepoint)
        activity_log("activity.cleared", outcome="failure")
        frappe.log_error("Activity clear operation failed.", "AOS Clear Activity Failed")
        return fail("Failed to clear activity.", error="INTERNAL_ERROR")
