"""Shared user-block helpers.

AOS blocking is user-to-user and intentionally separate from reporting.
These helpers are read-focused and reusable by social, profile, chat, calls,
search, and any future feature that needs to know whether two users can
interact.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


from aos.api.shared.responses import fail
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_payload


BLOCK_NONE = "none"
BLOCKED_BY_ME = "blocked_by_me"
BLOCKED_ME = "blocked_me"
BLOCK_MUTUAL = "mutual_block"


def normalize_user(user: Any) -> str | None:
    value = str(user or "").strip()
    return value or None


def get_block_status(*, current_user: str | None, target_user: str | None) -> dict[str, Any]:
    """Return canonical block state without exposing internal account identity."""
    current_user = normalize_user(current_user)
    target_user = normalize_user(target_user)

    if not target_user:
        return _empty_block_status(target_user=None)
    if not current_user:
        return _empty_block_status(target_user=target_user)
    if current_user == target_user:
        relation = relationship_payload(
            target=target_user,
            is_self=True,
            outgoing=False,
            incoming=False,
            blocked_by_me=False,
            blocked_me=False,
        )
    else:
        outgoing, incoming, blocks = SocialRepository().relationship_sets(
            viewer=current_user,
            targets=[target_user],
        )
        blocked_by_me, blocked_me = blocks.get(target_user, (False, False))
        relation = relationship_payload(
            target=target_user,
            is_self=False,
            outgoing=target_user in outgoing,
            incoming=target_user in incoming,
            blocked_by_me=blocked_by_me,
            blocked_me=blocked_me,
        )

    return {
        key: relation[key]
        for key in (
            "target_user",
            "is_blocked_by_me",
            "has_blocked_me",
            "is_blocked",
            "block_status",
            "can_follow",
            "can_message",
            "can_call",
            "can_view_profile",
        )
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
    """Return users blocked in either direction using the canonical repository."""
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

    _outgoing, _incoming, blocks = SocialRepository().relationship_sets(
        viewer=current_user,
        targets=unique_users,
    )
    return {user for user, state in blocks.items() if state[0] or state[1]}


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
        "target_user": public_account_id_for_user(target_user),
        "is_blocked_by_me": False,
        "has_blocked_me": False,
        "is_blocked": False,
        "block_status": BLOCK_NONE,
        "can_follow": False,
        "can_message": False,
        "can_call": False,
        "can_view_profile": bool(target_user),
    }


def _blocked_message(*, status: dict[str, Any], action: str) -> str:
    block_status = status.get("block_status")

    if block_status == BLOCKED_BY_ME:
        return f"You cannot {action} a user you have blocked."

    if block_status == BLOCKED_ME:
        return f"You cannot {action} this user."

    if block_status == BLOCK_MUTUAL:
        return f"You cannot {action} this user."

    return "This interaction is blocked."
