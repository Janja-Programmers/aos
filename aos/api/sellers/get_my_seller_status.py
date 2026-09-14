"""Authenticated Seller status and capability API implementation."""
from __future__ import annotations
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS
from aos.services.sellers.service import SellerService


def get_my_seller_status_impl(**kwargs):
    user, err = require_login()
    if err:
        return err
    limited = rate_limit(key=rate_limit_key("sellers", "status", user), ttl_seconds=60, limit=RATE_LIMITS["get_status_user"], message="Too many seller-status requests. Please try again shortly.")
    if limited:
        return limited
    return run_seller_api(
        lambda: ok("Seller status fetched.", data=SellerService().get_status(user=user, payload=kwargs)),
        fallback="Failed to fetch seller status.", log_title="AOS Get My Seller Status Failed", transactional=False, operation_name="get_status",
    )
