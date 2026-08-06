"""Central Live account and Social access policy."""

from __future__ import annotations

from typing import Any

from aos.api.shared.blocking import is_blocked_between
from aos.services.social.repository import SocialRepository

from .errors import LiveError


class LivePolicy:
    def __init__(self) -> None:
        self.social = SocialRepository()

    def require_account_available(self, user: str) -> dict[str, Any]:
        state = self.social.account_state(user)
        if not state or not bool(state.get("enabled")) or bool(state.get("is_deleted")):
            raise LiveError("Account is unavailable.", code="LIVE_ACCESS_DENIED", http_status=403)
        if str(state.get("account_status") or "Active") != "Active":
            raise LiveError("Account is unavailable.", code="LIVE_ACCESS_DENIED", http_status=403)
        return state

    def lock_relationship(self, *, host_user: str, viewer: str | None) -> None:
        """Serialize access decisions with canonical Social block mutations."""
        if viewer and viewer != host_user:
            self.social.lock_account_pair(user_a=host_user, user_b=viewer)

    def require_view_access(self, *, host_user: str, viewer: str | None) -> None:
        self.require_account_available(host_user)
        if not viewer or viewer == host_user:
            return
        self.require_account_available(viewer)
        if is_blocked_between(viewer, host_user):
            raise LiveError("Live stream not found.", code="LIVE_NOT_FOUND", http_status=404)
