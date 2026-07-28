"""Authenticated review-history API implementation."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.reviews.api import run_review_api
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.service import ReviewService


def list_my_reviews_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("reviews", "list_my", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["list_private"],
        message="Too many review requests.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok("Your reviews fetched.", data=ReviewService().list_my(user=user, payload=kwargs)),
        fallback="Failed to fetch your reviews.",
        log_title="AOS List My Reviews Failed",
    )


def list_reviews_received_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("reviews", "list_received", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["list_private"],
        message="Too many review requests.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok("Received reviews fetched.", data=ReviewService().list_received(user=user, payload=kwargs)),
        fallback="Failed to fetch received reviews.",
        log_title="AOS List Received Reviews Failed",
    )
