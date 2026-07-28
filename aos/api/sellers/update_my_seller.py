"""Authenticated Seller storefront update API implementation."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS
from aos.services.sellers.service import SellerService


def update_my_seller_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(
        key=rate_limit_key("sellers", "update", user),
        ttl_seconds=60,
        limit=RATE_LIMITS["update"],
        message="Too many seller updates. Please try again shortly.",
    )
    if limited:
        return limited
    return run_seller_api(
        lambda: ok(
            "Seller profile updated successfully.",
            data=SellerService().update_storefront(user=user, payload=kwargs),
        ),
        fallback="Failed to update seller profile.",
        log_title="AOS Update Seller Failed",
    )
