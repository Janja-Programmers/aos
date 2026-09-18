"""Review withdrawal API implementation."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.reviews.api import run_review_api
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.service import ReviewService


def delete_review_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("reviews", "delete", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["delete"],
        message="Too many review deletion requests.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok("Review withdrawn.", data=ReviewService().withdraw(user=user, payload=kwargs)),
        fallback="Failed to withdraw review.",
        log_title="AOS Delete Review Failed",
    )
