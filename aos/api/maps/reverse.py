"""Public reverse-geocoding implementation."""

from __future__ import annotations

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.api import run_maps_api
from aos.services.maps.service import MapsService
from aos.services.maps.validation import validate_reverse_geocode_request

from .constants import REVERSE_GEOCODE_LIMIT_PER_MINUTE_PER_IP


def reverse_geocode_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:maps:reverse:ip:{request_ip()}",
        ttl_seconds=60,
        limit=REVERSE_GEOCODE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many reverse-geocoding requests. Please try again shortly.",
    )
    if limited:
        return limited
    def operation():
        request = validate_reverse_geocode_request(kwargs)
        return ok("Location resolved successfully.", data=MapsService().reverse(request))

    return run_maps_api(
        operation,
        fallback="Failed to resolve location.",
        log_title="AOS Reverse Geocode Failed",
        transactional=False,
    )
