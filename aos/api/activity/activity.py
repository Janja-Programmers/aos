"""Activity Center API implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from aos.services.activity_service import ActivityService, ACTIVITY_DOCTYPE, ACTIVE_STATUS

from .constants import (
    LIST_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
    HIDE_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
    CLEAR_ACTIVITY_LIMIT_PER_MINUTE_PER_USER,
    DEFAULT_ACTIVITY_LIMIT,
    MAX_ACTIVITY_LIMIT,
    ACTIVITY_GROUP_MAX_LEN,
    ACTIVITY_TYPE_MAX_LEN,
)


def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _validate_limit(value):
    limit = _to_int(value, DEFAULT_ACTIVITY_LIMIT)

    if limit <= 0:
        return None, fail("Limit must be greater than zero.", code="VALIDATION_ERROR")

    if limit > MAX_ACTIVITY_LIMIT:
        limit = MAX_ACTIVITY_LIMIT

    return limit, None


def _validate_start(value):
    start = _to_int(value, 0)
    return max(start, 0)


def _normalize_group(value: str | None):
    value = (value or "").strip()

    if not value:
        return None, None

    if len(value) > ACTIVITY_GROUP_MAX_LEN:
        return None, fail("Activity group is too long.", code="VALIDATION_ERROR")

    return ActivityService.normalize_group(value), None


def _normalize_type(value: str | None):
    value = (value or "").strip()

    if not value:
        return None, None

    if len(value) > ACTIVITY_TYPE_MAX_LEN:
        return None, fail("Activity type is too long.", code="VALIDATION_ERROR")

    return ActivityService.normalize_type(value), None


def list_activity_impl(**kwargs):
    """List current user's private Activity Center rows."""
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

    limit, err = _validate_limit(kwargs.get("limit"))
    if err:
        return err

    start = _validate_start(kwargs.get("start"))

    group, err = _normalize_group(
        kwargs.get("group") or kwargs.get("activity_group")
    )
    if err:
        return err

    activity_type, err = _normalize_type(
        kwargs.get("type") or kwargs.get("activity_type")
    )
    if err:
        return err

    filters = {
        "user": current_user,
        "status": ACTIVE_STATUS,
    }

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

    try:
        total = frappe.db.count(ACTIVITY_DOCTYPE, filters=filters)

        rows = frappe.get_all(
            ACTIVITY_DOCTYPE,
            filters=filters,
            fields=fields,
            order_by="last_occurrence_at desc, creation desc",
            limit_start=start,
            limit_page_length=limit,
        )

        items = [ActivityService.serialize_activity(row) for row in rows]

        return ok(
            "Activity fetched.",
            data={
                "items": items,
                "total": int(total or 0),
                "limit": limit,
                "start": start,
                "has_more": (start + len(items)) < int(total or 0),
                "group": group,
                "type": activity_type,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Activity Failed",
        )
        return fail("Failed to fetch activity.", code="INTERNAL_ERROR")


def hide_activity_impl(**kwargs):
    """Hide one activity item from current user's Activity Center."""
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

    activity_id = (kwargs.get("activity_id") or kwargs.get("id") or "").strip()
    if not activity_id:
        return fail("Activity ID is required.", code="VALIDATION_ERROR")

    try:
        hidden = ActivityService.hide_activity(
            user=current_user,
            activity_id=activity_id,
        )

        if not hidden:
            return fail("Activity not found.", code="NOT_FOUND")

        frappe.db.commit()
        return ok("Activity hidden.", data={"id": activity_id})

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Hide Activity Failed",
        )
        frappe.db.rollback()
        return fail("Failed to hide activity.", code="INTERNAL_ERROR")


def clear_activity_impl(**kwargs):
    """Clear matching activity items for the current user."""
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

    group, err = _normalize_group(
        kwargs.get("group") or kwargs.get("activity_group")
    )
    if err:
        return err

    activity_type, err = _normalize_type(
        kwargs.get("type") or kwargs.get("activity_type")
    )
    if err:
        return err

    try:
        cleared_count = ActivityService.clear_activity(
            user=current_user,
            activity_group=group,
            activity_type=activity_type,
        )

        frappe.db.commit()

        return ok(
            "Activity cleared.",
            data={
                "cleared_count": cleared_count,
                "group": group,
                "type": activity_type,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Clear Activity Failed",
        )
        frappe.db.rollback()
        return fail("Failed to clear activity.", code="INTERNAL_ERROR")
