"""Review viewer-state API implementation."""

from __future__ import annotations

from aos.api.shared.auth import optional_active_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.reviews.api import run_review_api
from aos.services.reviews.constants import RATE_LIMITS
from aos.services.reviews.service import ReviewService


def get_review_viewer_state_impl(**kwargs):
    viewer = optional_active_user()
    limited = rate_limit(
        key=rate_limit_key("reviews", "viewer_state", viewer or request_ip()),
        ttl_seconds=60,
        limit=RATE_LIMITS["viewer_state"],
        message="Too many review eligibility requests.",
    )
    if limited:
        return limited
    return run_review_api(
        lambda: ok(
            "Review viewer state fetched.",
            data=ReviewService().viewer_state(
                ad_id=kwargs.get("ad_id") or kwargs.get("ad"),
                viewer=viewer,
            ),
        ),
        fallback="Failed to fetch review viewer state.",
        log_title="AOS Review Viewer State Failed",
    )
