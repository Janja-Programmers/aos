"""Review update API implementation."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.moderation_service import enqueue_review_moderation
from aos.services.reviews.api import run_review_api
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.service import ReviewService


def update_review_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("reviews", "update", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["update"],
        message="Too many review updates. Please try again shortly.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok("Review updated and queued for moderation.", data=ReviewService().update(
            user=user,
            payload=kwargs,
            moderation_enqueue=enqueue_review_moderation,
        )),
        fallback="Failed to update review.",
        log_title="AOS Update Review Failed",
    )
