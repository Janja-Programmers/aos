"""Public relationship-status endpoint."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.social.api import run_social_api
from aos.services.social.constants import (
    RELATIONSHIP_FOLLOWED_BY,
    RELATIONSHIP_FOLLOWING,
    RELATIONSHIP_FRIENDS,
    RELATIONSHIP_NONE,
)
from aos.services.social.observability import social_log
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


__all__ = [
    "RELATIONSHIP_NONE",
    "RELATIONSHIP_FOLLOWING",
    "RELATIONSHIP_FOLLOWED_BY",
    "RELATIONSHIP_FRIENDS",
    "get_relationship_status_impl",
]
