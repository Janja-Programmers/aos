"""Public Review listing API implementation."""

from __future__ import annotations

from aos.api.shared.auth import optional_active_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.reviews.api import run_review_api
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.service import ReviewService


def list_reviews_impl(**kwargs):
    viewer = optional_active_user()
    limited = rate_limit(
        key=rate_limit_key("reviews", "list", viewer or request_ip()),
        ttl_seconds=60,
        limit=RATE_LIMITS["list_public"],
        message="Too many review requests. Please try again shortly.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok("Reviews fetched.", data=ReviewService().list_public(payload=kwargs, viewer=viewer)),
        fallback="Failed to fetch reviews.",
        log_title="AOS List Reviews Failed",
    )
