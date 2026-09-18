"""Explicit Review reaction API implementations."""

from __future__ import annotations

from collections.abc import Callable

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.reviews.api import run_review_api
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.service import ReviewService


def _reaction_impl(*, operation: str, action: Callable[..., dict], kwargs: dict):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("reviews", f"reaction:{operation}", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["reaction"],
        message="Too many review reactions. Please try again shortly.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok("Review reaction updated.", data=action(user=user, payload=kwargs)),
        fallback="Failed to update review reaction.",
        log_title=f"AOS {operation.title()} Review Failed",
    )


def like_review_impl(**kwargs):
    return _reaction_impl(operation="like", action=ReviewService().like, kwargs=kwargs)


def unlike_review_impl(**kwargs):
    return _reaction_impl(operation="unlike", action=ReviewService().unlike, kwargs=kwargs)


def dislike_review_impl(**kwargs):
    return _reaction_impl(operation="dislike", action=ReviewService().dislike, kwargs=kwargs)


def undislike_review_impl(**kwargs):
    return _reaction_impl(operation="undislike", action=ReviewService().undislike, kwargs=kwargs)
