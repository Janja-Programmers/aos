"""Authenticated Maps routing implementations."""

from __future__ import annotations

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.api import run_maps_api
from aos.services.maps.service import MapsService
from aos.services.maps.validation import validate_refresh_route_request, validate_route_request

from .constants import (
    GET_ROUTE_LIMIT_PER_MINUTE_PER_IP,
    GET_ROUTE_LIMIT_PER_MINUTE_PER_USER,
    REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_IP,
    REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_USER,
)


def get_route_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = _route_rate_limit(
        user=user,
        operation="get_route",
        user_limit=GET_ROUTE_LIMIT_PER_MINUTE_PER_USER,
        ip_limit=GET_ROUTE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many route requests. Please try again shortly.",
    )
    if limited:
        return limited
    def operation():
        request = validate_route_request(kwargs)
        return ok("Route fetched successfully.", data=MapsService().route(request, viewer=user))

    return run_maps_api(
        operation,
        fallback="Failed to calculate route.",
        log_title="AOS Get Route Failed",
        transactional=False,
    )


def refresh_route_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = _route_rate_limit(
        user=user,
        operation="refresh_route",
        user_limit=REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_USER,
        ip_limit=REFRESH_ROUTE_LIMIT_PER_MINUTE_PER_IP,
        message="Too many route-refresh requests. Please try again shortly.",
    )
    if limited:
        return limited
    def operation():
        request = validate_refresh_route_request(kwargs)
        return ok("Route refreshed successfully.", data=MapsService().route(request, viewer=user))

    return run_maps_api(
        operation,
        fallback="Failed to refresh route.",
        log_title="AOS Refresh Route Failed",
        transactional=False,
    )


def _route_rate_limit(*, user: str, operation: str, user_limit: int, ip_limit: int, message: str):
    limited = rate_limit(
        key=rate_limit_key("maps", operation, user),
        ttl_seconds=60,
        limit=user_limit,
        message=message,
    )
    if limited:
        return limited
    return rate_limit(
        key=f"aos:maps:{operation}:ip:{request_ip()}",
        ttl_seconds=60,
        limit=ip_limit,
        message=message,
    )
