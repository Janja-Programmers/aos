"""Seller-owned explicit location mutation using Maps geocoding."""
from __future__ import annotations
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.service import MapsService
from aos.services.maps.validation import validate_set_seller_location_request
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS
from aos.services.sellers.location import SellerLocationService


def set_my_seller_location_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    for key, limit in (
        (rate_limit_key("sellers", "set_location_user", user), RATE_LIMITS["set_location_user"]),
        (rate_limit_key("sellers", "set_location_ip", request_ip()), RATE_LIMITS["set_location_ip"]),
    ):
        limited = rate_limit(key=key, ttl_seconds=60, limit=limit, message="Too many location updates. Please try again shortly.")
        if limited:
            return limited

    def operation():
        request = validate_set_seller_location_request(kwargs)
        seller_locations = SellerLocationService()
        resolved = None
        # Do not call an external geocoder for an exact idempotent retry.
        if seller_locations.needs_resolution(user=user, request=request):
            resolved = MapsService().resolve_location(latitude=request["latitude"], longitude=request["longitude"], language="en")
        return ok("Seller location saved successfully.", data=seller_locations.set_location(user=user, request=request, resolved_location=resolved))

    return run_seller_api(operation, fallback="Failed to save seller location.", log_title="AOS Set Seller Location Failed", transactional=True, operation_name="set_location")
