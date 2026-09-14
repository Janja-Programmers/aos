"""Seller-owned explicit location removal API."""
from __future__ import annotations
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.validation import validate_remove_seller_location_request
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS
from aos.services.sellers.location import SellerLocationService


def remove_my_seller_location_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    for key, limit in (
        (rate_limit_key("sellers", "remove_location_user", user), RATE_LIMITS["remove_location_user"]),
        (rate_limit_key("sellers", "remove_location_ip", request_ip()), RATE_LIMITS["remove_location_ip"]),
    ):
        limited = rate_limit(key=key, ttl_seconds=60, limit=limit, message="Too many location removal requests. Please try again shortly.")
        if limited:
            return limited
    def operation():
        request = validate_remove_seller_location_request(kwargs)
        return ok("Seller location removed successfully.", data=SellerLocationService().remove_location(user=user, request=request))
    return run_seller_api(operation, fallback="Failed to remove seller location.", log_title="AOS Remove Seller Location Failed", transactional=True, operation_name="remove_location")
