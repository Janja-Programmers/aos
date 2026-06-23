"""User block APIs.

Phase 1 provides the block foundation:
- block a user
- unblock a user
- fetch block status
- list users I blocked

Enforcement across follow/search/profile/chat/calls is handled in later phases.
"""

from __future__ import annotations

import re
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.account_status import is_account_deleted
from aos.api.shared.auth import require_login
from aos.api.shared.blocking import (
    BLOCK_STATUS_ACTIVE,
    BLOCK_STATUS_UNBLOCKED,
    USER_BLOCK_DOCTYPE,
    get_block_status,
)
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display_map

from .constants import (
    BLOCK_REASON_MAX_LEN,
    BLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
    GET_BLOCK_STATUS_LIMIT_PER_MINUTE_PER_USER,
    LIST_BLOCKED_USERS_LIMIT_PER_MINUTE_PER_USER,
    MAX_BLOCKED_USERS_LIMIT,
    DEFAULT_BLOCKED_USERS_LIMIT,
    UNBLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
)


def block_user_impl(**kwargs):
    """Block a target user and remove existing follow relationships both ways."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:block:user:{current_user}",
        ttl_seconds=60,
        limit=BLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
        message="Too many block requests. Please try again shortly.",
    )
    if rl:
        return rl

    target_user, err = _validate_target_user(
        current_user=current_user,
        target_user=kwargs.get("target_user"),
    )
    if err:
        return err

    reason, err = _normalize_reason(kwargs.get("reason"))
    if err:
        return err

    try:
        existing_active = frappe.db.get_value(
            USER_BLOCK_DOCTYPE,
            {
                "blocker_user": current_user,
                "blocked_user": target_user,
                "status": BLOCK_STATUS_ACTIVE,
            },
            "name",
        )

        if existing_active:
            # Keep block idempotent. Update reason when supplied, but do not fail.
            if reason:
                frappe.db.set_value(
                    USER_BLOCK_DOCTYPE,
                    existing_active,
                    "reason",
                    reason,
                    update_modified=True,
                )

            _remove_follow_relationships_and_sync(
                current_user=current_user,
                target_user=target_user,
            )

            frappe.db.commit()

            return ok(
                "User already blocked.",
                data={
                    "status": "blocked",
                    **get_block_status(
                        current_user=current_user,
                        target_user=target_user,
                    ),
                },
            )

        existing_inactive = frappe.db.get_value(
            USER_BLOCK_DOCTYPE,
            {
                "blocker_user": current_user,
                "blocked_user": target_user,
                "status": BLOCK_STATUS_UNBLOCKED,
            },
            "name",
            order_by="modified desc",
        )

        if existing_inactive:
            doc = frappe.get_doc(USER_BLOCK_DOCTYPE, existing_inactive)
            doc.status = BLOCK_STATUS_ACTIVE
            doc.reason = reason or doc.reason
            doc.blocked_at = now_datetime()
            doc.unblocked_at = None
            doc.save(ignore_permissions=True)
            block_id = doc.name
        else:
            doc = frappe.new_doc(USER_BLOCK_DOCTYPE)
            doc.blocker_user = current_user
            doc.blocked_user = target_user
            doc.reason = reason
            doc.status = BLOCK_STATUS_ACTIVE
            doc.blocked_at = now_datetime()
            doc.insert(ignore_permissions=True)
            block_id = doc.name

        _remove_follow_relationships_and_sync(
            current_user=current_user,
            target_user=target_user,
        )

        frappe.db.commit()

        return ok(
            "User blocked successfully.",
            data={
                "id": block_id,
                "status": "blocked",
                **get_block_status(
                    current_user=current_user,
                    target_user=target_user,
                ),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Block User Failed")
        return fail("Failed to block user.", code="INTERNAL_ERROR")


def unblock_user_impl(**kwargs):
    """Unblock a target user."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:unblock:user:{current_user}",
        ttl_seconds=60,
        limit=UNBLOCK_USER_LIMIT_PER_MINUTE_PER_USER,
        message="Too many unblock requests. Please try again shortly.",
    )
    if rl:
        return rl

    target_user, err = _validate_target_user(
        current_user=current_user,
        target_user=kwargs.get("target_user"),
        allow_deleted_target=True,
        allow_disabled_target=True,
    )
    if err:
        return err

    try:
        existing_active = frappe.db.get_value(
            USER_BLOCK_DOCTYPE,
            {
                "blocker_user": current_user,
                "blocked_user": target_user,
                "status": BLOCK_STATUS_ACTIVE,
            },
            "name",
        )

        if not existing_active:
            return ok(
                "User is not blocked.",
                data={
                    "status": "unblocked",
                    **get_block_status(
                        current_user=current_user,
                        target_user=target_user,
                    ),
                },
            )

        doc = frappe.get_doc(USER_BLOCK_DOCTYPE, existing_active)
        doc.status = BLOCK_STATUS_UNBLOCKED
        doc.unblocked_at = now_datetime()
        doc.save(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "User unblocked successfully.",
            data={
                "status": "unblocked",
                **get_block_status(
                    current_user=current_user,
                    target_user=target_user,
                ),
            },
        )

    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "AOS Unblock User Failed")
        return fail("Failed to unblock user.", code="INTERNAL_ERROR")


def get_block_status_impl(**kwargs):
    """Return block state between current user and target user."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:block:status:user:{current_user}",
        ttl_seconds=60,
        limit=GET_BLOCK_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    target_user, err = _validate_target_user(
        current_user=current_user,
        target_user=kwargs.get("target_user"),
        allow_deleted_target=True,
        allow_disabled_target=True,
    )
    if err:
        return err

    return ok(
        "Block status fetched successfully.",
        data=get_block_status(
            current_user=current_user,
            target_user=target_user,
        ),
    )


def list_blocked_users_impl(**kwargs):
    """List users blocked by the current user."""
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:block:list:user:{current_user}",
        ttl_seconds=60,
        limit=LIST_BLOCKED_USERS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = _get_limit(kwargs)
    start = _get_start(kwargs)

    try:
        rows = frappe.db.sql(
            f"""
            SELECT
                name,
                blocked_user,
                reason,
                blocked_at,
                creation
            FROM `tab{USER_BLOCK_DOCTYPE}`
            WHERE blocker_user = %s
              AND status = %s
            ORDER BY blocked_at DESC, creation DESC
            LIMIT %s OFFSET %s
            """,
            (current_user, BLOCK_STATUS_ACTIVE, limit, start),
            as_dict=True,
        )

        total = frappe.db.count(
            USER_BLOCK_DOCTYPE,
            {
                "blocker_user": current_user,
                "status": BLOCK_STATUS_ACTIVE,
            },
        )

        display_by_user = get_user_display_map([row.blocked_user for row in rows])

        items = []
        for row in rows:
            display = display_by_user.get(row.blocked_user) or {}
            block_status = get_block_status(
                current_user=current_user,
                target_user=row.blocked_user,
            )

            items.append(
                {
                    "id": row.name,
                    "blocked_user": row.blocked_user,
                    "user": row.blocked_user,
                    "reason": row.reason or "",
                    "blocked_at": row.blocked_at,
                    **display,
                    **block_status,
                }
            )

        return ok(
            "Blocked users fetched successfully.",
            data={
                "items": items,
                "total": int(total or 0),
                "limit": limit,
                "start": start,
                "has_more": start + len(items) < int(total or 0),
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Blocked Users Failed")
        return fail("Failed to fetch blocked users.", code="INTERNAL_ERROR")


def _validate_target_user(
    *,
    current_user: str,
    target_user: Any,
    allow_deleted_target: bool = False,
    allow_disabled_target: bool = False,
):
    target_user = str(target_user or "").strip()

    if not target_user:
        return None, fail("Target user is required.", code="VALIDATION_ERROR")

    if target_user == current_user:
        return None, fail("You cannot block yourself.", code="VALIDATION_ERROR")

    if not frappe.db.exists("User", target_user):
        return None, fail("User not found.", code="NOT_FOUND")

    if not frappe.db.exists("AOS Profile", target_user):
        return None, fail("User profile not found.", code="PROFILE_NOT_FOUND")

    enabled = frappe.db.get_value("User", target_user, "enabled")
    if not allow_disabled_target and int(enabled or 0) != 1:
        return None, fail("User is disabled.", code="ACCOUNT_DISABLED", http_status=403)

    if not allow_deleted_target and is_account_deleted(target_user):
        return None, fail("User not found.", code="NOT_FOUND")

    return target_user, None


def _normalize_reason(value: Any):
    reason = re.sub(r"\s+", " ", str(value or "").strip())

    if len(reason) > BLOCK_REASON_MAX_LEN:
        return None, fail(
            f"Reason is too long. Maximum is {BLOCK_REASON_MAX_LEN} characters.",
            code="VALIDATION_ERROR",
        )

    return reason, None


def _remove_follow_relationships_and_sync(*, current_user: str, target_user: str):
    """Remove follow rows both ways and recalculate both profile counters."""
    frappe.db.sql(
        """
        DELETE FROM `tabAOS Follow`
        WHERE (follower_user = %s AND following_user = %s)
           OR (follower_user = %s AND following_user = %s)
        """,
        (current_user, target_user, target_user, current_user),
    )

    _sync_profile_totals(current_user)
    _sync_profile_totals(target_user)


def _sync_profile_totals(user: str):
    total_followers = frappe.db.count(
        "AOS Follow",
        {
            "following_user": user,
        },
    )

    total_following = frappe.db.count(
        "AOS Follow",
        {
            "follower_user": user,
        },
    )

    frappe.db.set_value(
        "AOS Profile",
        user,
        {
            "total_followers": int(total_followers or 0),
            "total_following": int(total_following or 0),
        },
        update_modified=False,
    )


def _get_limit(kwargs) -> int:
    try:
        limit = int(kwargs.get("limit") or DEFAULT_BLOCKED_USERS_LIMIT)
    except Exception:
        limit = DEFAULT_BLOCKED_USERS_LIMIT

    if limit < 1:
        limit = DEFAULT_BLOCKED_USERS_LIMIT

    return min(limit, MAX_BLOCKED_USERS_LIMIT)


def _get_start(kwargs) -> int:
    try:
        start = int(kwargs.get("start") or 0)
    except Exception:
        start = 0

    return max(start, 0)
