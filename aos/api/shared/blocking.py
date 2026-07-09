"""Shared user-block helpers.

AOS blocking is user-to-user and intentionally separate from reporting.
These helpers are read-focused and reusable by social, profile, chat, calls,
search, and any future feature that needs to know whether two users can
interact.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import frappe

from aos.api.shared.responses import fail


USER_BLOCK_DOCTYPE = "AOS User Block"
BLOCK_STATUS_ACTIVE = "Active"
BLOCK_STATUS_UNBLOCKED = "Unblocked"

BLOCK_NONE = "none"
BLOCKED_BY_ME = "blocked_by_me"
BLOCKED_ME = "blocked_me"
BLOCK_MUTUAL = "mutual_block"


def normalize_user(user: Any) -> str | None:
    value = str(user or "").strip()
    return value or None


def get_block_status(*, current_user: str | None, target_user: str | None) -> dict[str, Any]:
    """Return block state between current_user and target_user."""
    current_user = normalize_user(current_user)
    target_user = normalize_user(target_user)

    if not current_user or not target_user or current_user == target_user:
        return _empty_block_status(target_user=target_user)

    is_blocked_by_me = _active_block_exists(
        blocker_user=current_user,
        blocked_user=target_user,
    )
    has_blocked_me = _active_block_exists(
        blocker_user=target_user,
        blocked_user=current_user,
    )

    block_status = _resolve_block_status(
        is_blocked_by_me=is_blocked_by_me,
        has_blocked_me=has_blocked_me,
    )

    is_blocked = block_status != BLOCK_NONE

    return {
        "target_user": target_user,
        "is_blocked_by_me": is_blocked_by_me,
        "has_blocked_me": has_blocked_me,
        "is_blocked": is_blocked,
        "block_status": block_status,
        "can_follow": not is_blocked,
        "can_message": not is_blocked,
        "can_call": not is_blocked,
        # If I blocked them, the frontend can still open a limited profile so I
        # can unblock. If they blocked me, their profile should be unavailable.
        "can_view_profile": not has_blocked_me,
    }


def is_blocked_between(user_a: str | None, user_b: str | None) -> bool:
    """Return True if either user has an active block against the other."""
    return bool(
        get_block_status(
            current_user=user_a,
            target_user=user_b,
        ).get("is_blocked")
    )


def ensure_not_blocked(*, current_user: str | None, target_user: str | None, action: str = "interact"):
    """Return fail response if either user has blocked the other, else None."""
    status = get_block_status(
        current_user=current_user,
        target_user=target_user,
    )

    if not status.get("is_blocked"):
        return None

    return fail(
        _blocked_message(status=status, action=action),
        error="USER_BLOCKED",
        data=status,
        http_status=403,
    )


def get_blocked_user_set(current_user: str | None, users: Iterable[str]) -> set[str]:
    """Return users that current_user cannot interact with due to a block.

    Includes both directions:
      - current_user blocked returned user
      - returned user blocked current_user
    """
    current_user = normalize_user(current_user)
    unique_users = sorted(
        {
            normalized
            for normalized in (normalize_user(user) for user in users)
            if normalized and normalized != current_user
        }
    )

    if not current_user or not unique_users:
        return set()

    rows = frappe.db.sql(
        f"""
        SELECT blocker_user, blocked_user
        FROM `tab{USER_BLOCK_DOCTYPE}`
        WHERE status = %(status)s
          AND (
                (blocker_user = %(current_user)s AND blocked_user IN %(users)s)
             OR (blocked_user = %(current_user)s AND blocker_user IN %(users)s)
          )
        """,
        {
            "status": BLOCK_STATUS_ACTIVE,
            "current_user": current_user,
            "users": tuple(unique_users),
        },
        as_dict=True,
    )

    blocked: set[str] = set()

    for row in rows:
        blocker = normalize_user(row.blocker_user)
        blocked_user = normalize_user(row.blocked_user)

        if blocker == current_user and blocked_user:
            blocked.add(blocked_user)
        elif blocked_user == current_user and blocker:
            blocked.add(blocker)

    return blocked


def filter_blocked_users(current_user: str | None, users: Iterable[str]) -> list[str]:
    """Return users excluding anyone blocked in either direction."""
    normalized_users = [
        user
        for user in (normalize_user(user) for user in users)
        if user
    ]

    blocked = get_blocked_user_set(current_user, normalized_users)
    return [user for user in normalized_users if user not in blocked]


def _empty_block_status(*, target_user: str | None = None) -> dict[str, Any]:
    return {
        "target_user": target_user,
        "is_blocked_by_me": False,
        "has_blocked_me": False,
        "is_blocked": False,
        "block_status": BLOCK_NONE,
        "can_follow": True,
        "can_message": True,
        "can_call": True,
        "can_view_profile": True,
    }


def _active_block_exists(*, blocker_user: str, blocked_user: str) -> bool:
    return bool(
        frappe.db.exists(
            USER_BLOCK_DOCTYPE,
            {
                "blocker_user": blocker_user,
                "blocked_user": blocked_user,
                "status": BLOCK_STATUS_ACTIVE,
            },
        )
    )


def _resolve_block_status(*, is_blocked_by_me: bool, has_blocked_me: bool) -> str:
    if is_blocked_by_me and has_blocked_me:
        return BLOCK_MUTUAL

    if is_blocked_by_me:
        return BLOCKED_BY_ME

    if has_blocked_me:
        return BLOCKED_ME

    return BLOCK_NONE


def _blocked_message(*, status: dict[str, Any], action: str) -> str:
    block_status = status.get("block_status")

    if block_status == BLOCKED_BY_ME:
        return f"You cannot {action} a user you have blocked."

    if block_status == BLOCKED_ME:
        return f"You cannot {action} this user."

    if block_status == BLOCK_MUTUAL:
        return f"You cannot {action} this user."

    return "This interaction is blocked."
