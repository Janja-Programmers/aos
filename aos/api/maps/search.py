"""Public place-search implementation."""

from __future__ import annotations

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.api import run_maps_api
from aos.services.maps.service import MapsService
from aos.services.maps.validation import validate_search_request

from .constants import SEARCH_PLACES_LIMIT_PER_MINUTE_PER_IP


def search_places_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:maps:search:ip:{request_ip()}",
        ttl_seconds=60,
        limit=SEARCH_PLACES_LIMIT_PER_MINUTE_PER_IP,
        message="Too many place-search requests. Please try again shortly.",
    )
    if limited:
        return limited
    def operation():
        request = validate_search_request(kwargs)
        return ok("Places fetched successfully.", data=MapsService().search(request))

    return run_maps_api(
        operation,
        fallback="Failed to fetch places.",
        log_title="AOS Search Places Failed",
        transactional=False,
    )
