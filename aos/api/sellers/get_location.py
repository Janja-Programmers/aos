"""Seller-owned location read API."""
from __future__ import annotations
from aos.api.shared.auth import current_user, require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.validation import validate_get_seller_location_request
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS
from aos.services.sellers.location import SellerLocationService


def get_seller_location_impl(**kwargs):
    def operation():
        request = validate_get_seller_location_request(kwargs)
        seller_id = request["seller_reference"] or None
        viewer = current_user()
        viewer = viewer if viewer and viewer != "Guest" else None
        user = viewer
        if not seller_id:
            user, error = require_login()
            if error:
                return error
        limited = rate_limit(
            key=rate_limit_key("sellers", "get_location_ip", request_ip()), ttl_seconds=60,
            limit=RATE_LIMITS["get_location_ip"], message="Too many seller-location requests. Please try again shortly.",
        )
        if limited:
            return limited
        if user:
            limited = rate_limit(
                key=rate_limit_key("sellers", "get_location_user", user), ttl_seconds=60,
                limit=RATE_LIMITS["get_location_user"], message="Too many seller-location requests. Please try again shortly.",
            )
            if limited:
                return limited
        return ok("Seller location fetched successfully.", data=SellerLocationService().get_location(user=user, seller_id=seller_id, viewer=viewer))
    return run_seller_api(operation, fallback="Failed to fetch seller location.", log_title="AOS Get Seller Location Failed", transactional=False, operation_name="get_location")
