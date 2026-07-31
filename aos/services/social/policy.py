"""Central Social authorization, lifecycle, and visibility policy."""

from __future__ import annotations

from typing import Any

from .constants import ACCOUNT_ACTIVE
from .errors import SocialNotFoundError, SocialPermissionError, SocialValidationError
from .repository import SocialRepository


class SocialPolicy:
    def __init__(self, repository: SocialRepository):
        self.repository = repository

    def require_actor(self, user: str) -> dict[str, Any]:
        state = self.repository.account_state(user)
        if not state:
            raise SocialPermissionError("Social action is unavailable.", code="SOCIAL_ACTOR_UNAVAILABLE")
        if int(state.get("enabled") or 0) != 1 or state.get("account_status") != ACCOUNT_ACTIVE or int(state.get("is_deleted") or 0):
            raise SocialPermissionError("Social action is unavailable.", code="SOCIAL_ACTOR_UNAVAILABLE")
        return state

    def require_target(self, *, actor: str, target: str, operation: str, allow_inactive: bool = False) -> dict[str, Any]:
        if actor == target:
            raise SocialValidationError(
                f"You cannot {operation} your own account.",
                code="SOCIAL_SELF_ACTION",
            )
        state = self.repository.account_state(target)
        if not state:
            raise SocialNotFoundError("Profile unavailable.")
        if not allow_inactive and (
            int(state.get("enabled") or 0) != 1
            or state.get("account_status") != ACCOUNT_ACTIVE
            or int(state.get("is_deleted") or 0)
        ):
            raise SocialNotFoundError("Profile unavailable.")
        return state
