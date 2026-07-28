"""Public Seller discovery API implementation."""

from __future__ import annotations

from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS
from aos.services.sellers.service import SellerService


def list_sellers_impl(**kwargs):
    viewer = current_user()
    viewer = viewer if viewer and viewer != "Guest" else None
    limited = rate_limit(
        key=rate_limit_key("sellers", "list", viewer or request_ip()),
        ttl_seconds=60,
        limit=RATE_LIMITS["list_public"],
        message="Too many seller requests. Please try again shortly.",
    )
    if limited:
        return limited
    return run_seller_api(
        lambda: ok(
            "Sellers fetched successfully.",
            data=SellerService().list_public(payload=kwargs, viewer=viewer),
        ),
        fallback="Failed to fetch sellers.",
        log_title="AOS List Sellers Failed",
        transactional=False,
    )
