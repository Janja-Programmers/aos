"""Public place autocomplete implementation."""

from __future__ import annotations

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.api import run_maps_api
from aos.services.maps.service import MapsService
from aos.services.maps.validation import validate_autocomplete_request

from .constants import AUTOCOMPLETE_PLACES_LIMIT_PER_MINUTE_PER_IP


def autocomplete_places_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:maps:autocomplete:ip:{request_ip()}",
        ttl_seconds=60,
        limit=AUTOCOMPLETE_PLACES_LIMIT_PER_MINUTE_PER_IP,
        message="Too many autocomplete requests. Please try again shortly.",
    )
    if limited:
        return limited
    def operation():
        request = validate_autocomplete_request(kwargs)
        return ok("Place suggestions fetched successfully.", data=MapsService().autocomplete(request))

    return run_maps_api(
        operation,
        fallback="Failed to fetch place suggestions.",
        log_title="AOS Autocomplete Places Failed",
        transactional=False,
    )
