"""Private Activity Center hooks for canonical Social/Search mutations.

Social and Search remain authoritative.  This module only emits the bounded
Activity events defined by the Activity taxonomy after their owning mutation
has succeeded.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.user_display import get_user_display
from aos.services.activity.producer import best_effort_activity
from aos.services.activity_service import ActivityService

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
    return best_effort_activity(action_name, fn, *args, **kwargs)


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
    # The report id is used only as the hidden one-off producer identity.  The
    # Activity row still references the reported User/Profile, not Reports.
    return ActivityService.build_unique_key(
        activity_type=USER_REPORT_ACTIVITY,
        target_doctype=USER_REPORT_DOCTYPE,
        target_name=report_id,
        route_type=ROUTE_TYPE_PROFILE,
        route_id=report_id,
    )


def _load_user_target(target_user: str | None) -> dict[str, Any] | None:
    target_user = _normalize_user(target_user)
    if not target_user or not frappe.db.exists(USER_DOCTYPE, target_user):
        return None

    display = get_user_display(target_user)
    public_user = str(display.get("account_id") or "").strip()
    if not public_user:
        return None

    title = _compact_text(display.get("display_name"), max_len=120) or "User"
    return {
        # Internal target identity is never serialized.  route_id is the
        # canonical public Accounts identity exposed to clients.
        "target_doctype": USER_DOCTYPE,
        "target_name": target_user,
        "target_title": title,
        "target_subtitle": "Profile",
        "target_image": display.get("avatar") or "",
        "route_type": ROUTE_TYPE_PROFILE,
        "route_id": public_user,
        "metadata": {"target_user": public_user},
    }


def record_user_search_activity(
    *,
    user: str | None,
    query: str,
    result_count: int | None = None,
) -> str | None:
    """Coalesce one private user-search history item by normalized query."""
    query = _normalize_query(query)
    if not user or not query:
        return None

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
        metadata={"query": query, "result_count": max(0, int(result_count or 0))},
        unique_key=user_search_unique_key(query),
    )


def record_follow_user_activity(*, user: str | None, target_user: str) -> str | None:
    """Coalesce follow history for the successfully followed account."""
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
    """Coalesce block history for the successfully blocked account."""
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
    """Record one idempotent private user-report history item."""
    if not user or not report_id:
        return None
    target = _load_user_target(target_user)
    if not target:
        return None
    metadata = target.pop("metadata", None) or {}
    metadata["reason"] = _compact_text(reason, max_len=200)
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
