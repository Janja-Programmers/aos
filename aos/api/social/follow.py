"""Public follow/unfollow API boundaries."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.social.api import run_social_api
from aos.services.social.observability import social_log
from aos.services.social.service import SocialService

from .activity import record_follow_user_activity
from .constants import FOLLOW_LIMIT_PER_MINUTE_PER_USER, UNFOLLOW_LIMIT_PER_MINUTE_PER_USER


def follow_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:social:follow:user:{current_user}",
        ttl_seconds=60,
        limit=FOLLOW_LIMIT_PER_MINUTE_PER_USER,
        message="Too many follow requests. Please try again shortly.",
    )
    if limited:
        social_log("follow", outcome="rejected", reason="rate_limit")
        return limited

    def operation():
        data = SocialService().follow(actor=current_user, payload=kwargs, activity_callback=record_follow_user_activity)
        return ok("Followed successfully." if data.get("changed") else "Already following.", data=data)

    return run_social_api(operation, fallback="Failed to follow account.", log_title="AOS Follow Failed", operation_name="follow")


def unfollow_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:social:unfollow:user:{current_user}",
        ttl_seconds=60,
        limit=UNFOLLOW_LIMIT_PER_MINUTE_PER_USER,
        message="Too many unfollow requests. Please try again shortly.",
    )
    if limited:
        social_log("unfollow", outcome="rejected", reason="rate_limit")
        return limited

    def operation():
        data = SocialService().unfollow(actor=current_user, payload=kwargs)
        return ok("Unfollowed successfully." if data.get("changed") else "Already not following.", data=data)

    return run_social_api(operation, fallback="Failed to unfollow account.", log_title="AOS Unfollow Failed", operation_name="unfollow")
