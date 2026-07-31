"""Thin relationship endpoint and compatibility projection helper."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.social.api import run_social_api
from aos.services.social.observability import social_log
from aos.services.social.constants import (
    RELATIONSHIP_FOLLOWED_BY,
    RELATIONSHIP_FOLLOWING,
    RELATIONSHIP_FRIENDS,
    RELATIONSHIP_NONE,
)
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map, relationship_payload
from aos.services.social.service import SocialService

from .constants import GET_RELATIONSHIP_STATUS_LIMIT_PER_MINUTE_PER_USER


def get_relationship_status_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:follow:relationship:user:{current_user}",
        ttl_seconds=60,
        limit=GET_RELATIONSHIP_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many relationship requests. Please try again shortly.",
    )
    if limited:
        social_log("relationship", outcome="rejected", reason="rate_limit")
        return limited
    return run_social_api(
        lambda: ok(
            "Relationship status fetched successfully.",
            data=SocialService().relationship_from_payload(actor=current_user, payload=kwargs),
        ),
        fallback="Failed to get relationship status.",
        log_title="AOS Get Relationship Status Failed",
        operation_name="relationship",
    )


def build_relationship_status(*, current_user: str, target_user: str) -> dict:
    """Compatibility helper used by profile, seller, live, and shorts serializers."""
    if current_user == target_user:
        return relationship_payload(
            target=target_user,
            is_self=True,
            outgoing=False,
            incoming=False,
            blocked_by_me=False,
            blocked_me=False,
        )
    mapping = relationship_map(
        repository=SocialRepository(),
        viewer=current_user,
        targets=[target_user],
    )
    return mapping.get(target_user) or relationship_payload(
        target=target_user,
        is_self=False,
        outgoing=False,
        incoming=False,
        blocked_by_me=False,
        blocked_me=False,
    )


__all__ = [
    "RELATIONSHIP_NONE",
    "RELATIONSHIP_FOLLOWING",
    "RELATIONSHIP_FOLLOWED_BY",
    "RELATIONSHIP_FRIENDS",
    "build_relationship_status",
    "get_relationship_status_impl",
]
