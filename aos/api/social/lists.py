"""Thin Social list endpoints using the canonical service."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.social.api import run_social_api
from aos.services.social.observability import social_log
from aos.services.social.service import SocialService

from .constants import (
    GET_FOLLOWERS_LIMIT_PER_MINUTE_PER_USER,
    GET_FOLLOWING_LIMIT_PER_MINUTE_PER_USER,
    GET_FRIENDS_LIMIT_PER_MINUTE_PER_USER,
)


def _run(*, mode: str, kwargs: dict, rate: int, message: str):
    current_user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=f"aos:follow:{mode}:user:{current_user}",
        ttl_seconds=60,
        limit=rate,
        message="Too many Social list requests. Please try again shortly.",
    )
    if limited:
        social_log(f"{mode}_list", outcome="rejected", reason="rate_limit")
        return limited
    return run_social_api(
        lambda: ok(message, data=SocialService().list_relationships(actor=current_user, payload=kwargs, mode=mode)),
        fallback=f"Failed to fetch {mode}.",
        log_title=f"AOS Get {mode.title()} Failed",
        operation_name=f"{mode}_list",
    )


def get_following_impl(**kwargs):
    return _run(mode="following", kwargs=kwargs, rate=GET_FOLLOWING_LIMIT_PER_MINUTE_PER_USER, message="Following fetched successfully.")


def get_followers_impl(**kwargs):
    return _run(mode="followers", kwargs=kwargs, rate=GET_FOLLOWERS_LIMIT_PER_MINUTE_PER_USER, message="Followers fetched successfully.")


def get_friends_impl(**kwargs):
    return _run(mode="friends", kwargs=kwargs, rate=GET_FRIENDS_LIMIT_PER_MINUTE_PER_USER, message="Friends fetched successfully.")
