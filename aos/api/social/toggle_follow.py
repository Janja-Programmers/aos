"""Thin compatibility boundary for follow/unfollow."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.social.api import run_social_api
from aos.services.social.observability import social_log
from aos.services.social.service import SocialService

from .activity import record_follow_user_activity
from .constants import TOGGLE_FOLLOW_LIMIT_PER_MINUTE_PER_USER


def toggle_follow_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:follow:toggle:user:{current_user}",
        ttl_seconds=60,
        limit=TOGGLE_FOLLOW_LIMIT_PER_MINUTE_PER_USER,
        message="Too many follow requests. Please try again shortly.",
    )
    if limited:
        social_log("toggle_follow", outcome="rejected", reason="rate_limit")
        return limited

    def operation():
        data = SocialService().toggle_follow(
            actor=current_user,
            payload=kwargs,
            activity_callback=record_follow_user_activity,
        )
        if data["status"] == "followed":
            message = "Followed successfully." if data.get("changed") else "Already followed."
        else:
            message = "Unfollowed successfully." if data.get("changed") else "Already unfollowed."
        return ok(message, data=data)

    return run_social_api(operation, fallback="Failed to update follow status.", log_title="AOS Toggle Follow Failed", operation_name="toggle_follow")
