"""Shared read-only access to the canonical Social block/capability contract."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from aos.api.shared.responses import fail
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.social.capabilities import SocialCapabilityService

BLOCK_NONE = "none"
BLOCKED_BY_ME = "blocked_by_me"
BLOCKED_ME = "blocked_me"
BLOCK_MUTUAL = "mutual_block"


def normalize_user(user: Any) -> str | None:
    value = str(user or "").strip()
    return value or None


def get_block_status(*, current_user: str | None, target_user: str | None) -> dict[str, Any]:
    """Return the shared capability projection without exposing internal User identity."""
    current_user = normalize_user(current_user)
    target_user = normalize_user(target_user)
    if not target_user:
        return _empty_block_status(target_user=None)
    if not current_user:
        return _empty_block_status(target_user=target_user)

    relation = SocialCapabilityService().relationship_projection(
        viewer=current_user,
        target=target_user,
    )
    return {
        key: relation[key]
        for key in (
            "account_id",
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
    return bool(get_block_status(current_user=user_a, target_user=user_b).get("is_blocked"))


def ensure_not_blocked(*, current_user: str | None, target_user: str | None, action: str = "interact"):
    status = get_block_status(current_user=current_user, target_user=target_user)
    if not status.get("is_blocked"):
        return None
    return fail(
        _blocked_message(status=status, action=action),
        error="USER_BLOCKED",
        data=status,
        http_status=403,
    )


def get_blocked_user_set(current_user: str | None, users: Iterable[str]) -> set[str]:
    current_user = normalize_user(current_user)
    unique_users = sorted({u for u in (normalize_user(user) for user in users) if u and u != current_user})
    if not current_user or not unique_users:
        return set()
    projections = SocialCapabilityService().projection_map(viewer=current_user, targets=unique_users)
    return {user for user, state in projections.items() if state.get("is_blocked")}


def filter_blocked_users(current_user: str | None, users: Iterable[str]) -> list[str]:
    normalized_users = [user for user in (normalize_user(user) for user in users) if user]
    blocked = get_blocked_user_set(current_user, normalized_users)
    return [user for user in normalized_users if user not in blocked]


def _empty_block_status(*, target_user: str | None = None) -> dict[str, Any]:
    return {
        "account_id": public_account_id_for_user(target_user),
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
    if block_status in {BLOCKED_ME, BLOCK_MUTUAL}:
        return f"You cannot {action} this user."
    return "This interaction is blocked."
