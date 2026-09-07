"""Activity Center hooks for Social/Search actions.

Social/search doctypes remain the source of truth; AOS User Activity is the
private user-facing history layer.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.user_display import get_user_display
from aos.services.activity_service import ActivityService
from aos.services.social.observability import social_log

USER_DOCTYPE = "User"
USER_REPORT_DOCTYPE = "AOS User Report"

SEARCH_ACTIVITY_GROUP = "Search"
SOCIAL_ACTIVITY_GROUP = "Social"

USER_SEARCH_ACTIVITY = "user_search"
USER_FOLLOW_ACTIVITY = "user_follow"
USER_BLOCK_ACTIVITY = "user_block"
USER_REPORT_ACTIVITY = "user_report"

ROUTE_TYPE_SEARCH_USERS = "user_search"
ROUTE_TYPE_PROFILE = "profile"


def _compact_text(value: str | None, *, max_len: int = 120) -> str:
    value = " ".join((value or "").strip().split())
    if not value:
        return ""

    if len(value) <= max_len:
        return value

    return value[: max_len - 1].rstrip() + "…"


def _normalize_query(value: str | None) -> str:
    return " ".join((value or "").strip().split())


def _normalize_user(value: str | None) -> str:
    return (value or "").strip()


def _safe_record(action_name: str, fn, *args, **kwargs) -> str | bool | None:
    try:
        return fn(*args, **kwargs)
    except Exception:
        operation = (
            "search" if action_name == "record_user_search_activity"
            else "block" if action_name == "record_block_user_activity"
            else "toggle_follow" if action_name == "record_follow_user_activity"
            else "relationship"
        )
        social_log(operation, outcome="failure", reason="internal")
        frappe.log_error(
            "Social activity hook failed.",
            "AOS Social Activity Hook Failed",
        )
        return None


def user_search_unique_key(query: str) -> str:
    normalized_query = _normalize_query(query).lower()
    return ActivityService.build_unique_key(
        activity_type=USER_SEARCH_ACTIVITY,
        route_type=ROUTE_TYPE_SEARCH_USERS,
        route_id=normalized_query,
    )


def user_follow_unique_key(target_user: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=USER_FOLLOW_ACTIVITY,
        target_doctype=USER_DOCTYPE,
        target_name=target_user,
        route_type=ROUTE_TYPE_PROFILE,
        route_id=target_user,
    )


def user_block_unique_key(target_user: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=USER_BLOCK_ACTIVITY,
        target_doctype=USER_DOCTYPE,
        target_name=target_user,
        route_type=ROUTE_TYPE_PROFILE,
        route_id=target_user,
    )


def user_report_unique_key(report_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=USER_REPORT_ACTIVITY,
        target_doctype=USER_REPORT_DOCTYPE,
        target_name=report_id,
        route_type=ROUTE_TYPE_PROFILE,
        route_id=report_id,
    )


def _load_user_target(target_user: str | None) -> dict[str, Any] | None:
    target_user = _normalize_user(target_user)
    if not target_user:
        return None

    if not frappe.db.exists(USER_DOCTYPE, target_user):
        return None

    display = get_user_display(target_user)
    public_user = display.get("account_id")
    title = _compact_text(display.get("display_name"), max_len=120) or "User"

    return {
        "target_doctype": USER_DOCTYPE,
        "target_name": public_user,
        "target_title": title,
        "target_subtitle": "Profile",
        "target_image": display.get("avatar") or display.get("user_image") or "",
        "route_type": ROUTE_TYPE_PROFILE,
        "route_id": public_user,
        "metadata": {
            "target_user": public_user,
            "target_display_name": title,
            "target_is_deleted": bool(display.get("is_deleted")),
            "target_is_live": bool(display.get("is_live")),
            "target_live_id": display.get("live_id"),
        },
    }


def record_user_search_activity(
    *,
    user: str | None,
    query: str,
    result_count: int | None = None,
) -> str | None:
    """Record/de-dupe a global user search query."""
    query = _normalize_query(query)
    if not user or not query:
        return None

    metadata = {
        "query": query,
        "result_count": int(result_count or 0),
    }

    return _safe_record(
        "record_user_search_activity",
        ActivityService.record_or_update_activity,
        user=user,
        activity_group=SEARCH_ACTIVITY_GROUP,
        activity_type=USER_SEARCH_ACTIVITY,
        target_title=query,
        target_subtitle="User search",
        route_type=ROUTE_TYPE_SEARCH_USERS,
        route_id=query,
        metadata=metadata,
        unique_key=user_search_unique_key(query),
    )


def record_follow_user_activity(
    *,
    user: str | None,
    target_user: str,
) -> str | None:
    """Record/de-dupe follow history for a target user."""
    if not user:
        return None

    target = _load_user_target(target_user)
    if not target:
        return None

    metadata = target.pop("metadata", None)

    return _safe_record(
        "record_follow_user_activity",
        ActivityService.record_or_update_activity,
        user=user,
        activity_group=SOCIAL_ACTIVITY_GROUP,
        activity_type=USER_FOLLOW_ACTIVITY,
        metadata=metadata,
        unique_key=user_follow_unique_key(target_user),
        **target,
    )


def record_block_user_activity(
    *,
    user: str | None,
    target_user: str,
    reason: str | None = None,
) -> str | None:
    """Record/de-dupe block history for a target user."""
    if not user:
        return None

    target = _load_user_target(target_user)
    if not target:
        return None

    metadata = target.pop("metadata", None) or {}
    metadata["reason"] = _compact_text(reason, max_len=200)

    return _safe_record(
        "record_block_user_activity",
        ActivityService.record_or_update_activity,
        user=user,
        activity_group=SOCIAL_ACTIVITY_GROUP,
        activity_type=USER_BLOCK_ACTIVITY,
        metadata=metadata,
        unique_key=user_block_unique_key(target_user),
        **target,
    )


def record_report_user_activity(
    *,
    user: str | None,
    target_user: str,
    report_id: str,
    reason: str | None = None,
) -> str | None:
    """Record one user-report history item."""
    if not user or not report_id:
        return None

    target = _load_user_target(target_user)
    if not target:
        return None

    metadata = target.pop("metadata", None) or {}
    metadata.update(
        {
            "report_id": report_id,
            "reason": reason,
        }
    )

    target["target_doctype"] = USER_REPORT_DOCTYPE
    target["target_name"] = report_id
    target["target_subtitle"] = _compact_text(reason, max_len=120) or "Reported user"

    return _safe_record(
        "record_report_user_activity",
        ActivityService.record_activity,
        user=user,
        activity_group=SOCIAL_ACTIVITY_GROUP,
        activity_type=USER_REPORT_ACTIVITY,
        metadata=metadata,
        unique_key=user_report_unique_key(report_id),
        **target,
    )
