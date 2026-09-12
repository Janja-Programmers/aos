"""Canonical internal Social capability boundary for other AOS domains."""

from __future__ import annotations

from typing import Any, Iterable

from .repository import SocialRepository
from .serializers import relationship_map, relationship_payload


class SocialCapabilityService:
    """Answer viewer-target relationship/capability questions without leaking storage details."""

    def __init__(self, repository: SocialRepository | None = None):
        self.repository = repository or SocialRepository()

    def relationship_projection(
        self,
        *,
        viewer: str,
        target: str,
        target_account_id: str | None = None,
    ) -> dict[str, Any]:
        if viewer == target:
            return relationship_payload(
                target=target,
                target_account_id=target_account_id,
                is_self=True,
                outgoing=False,
                incoming=False,
                blocked_by_me=False,
                blocked_me=False,
            )
        projection = relationship_map(repository=self.repository, viewer=viewer, targets=[target])[target]
        if target_account_id:
            projection["account_id"] = target_account_id
        return projection

    def projection_map(self, *, viewer: str, targets: Iterable[str]) -> dict[str, dict[str, Any]]:
        return relationship_map(repository=self.repository, viewer=viewer, targets=targets)

    def is_blocked(self, *, viewer: str, target: str) -> bool:
        return bool(self.relationship_projection(viewer=viewer, target=target).get("is_blocked"))

    def can_view_profile(self, *, viewer: str, target: str) -> bool:
        return bool(self.relationship_projection(viewer=viewer, target=target).get("can_view_profile"))

    def can_follow(self, *, viewer: str, target: str) -> bool:
        return bool(self.relationship_projection(viewer=viewer, target=target).get("can_follow"))

    def can_message(self, *, viewer: str, target: str) -> bool:
        return bool(self.relationship_projection(viewer=viewer, target=target).get("can_message"))

    def can_call(self, *, viewer: str, target: str) -> bool:
        return bool(self.relationship_projection(viewer=viewer, target=target).get("can_call"))
