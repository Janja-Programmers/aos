"""Canonical Seller-linked Maps implementations.

The public Seller endpoints remain compatibility wrappers while geospatial
ownership, validation and concurrency live in the Maps domain.
"""

from __future__ import annotations

from aos.api.shared.auth import current_user, require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.api.sellers.constants import (
    GET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
    GET_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_IP,
    LIST_SELLER_MAP_POINTS_LIMIT_PER_MINUTE_PER_IP,
    REMOVE_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
    SET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
)
from aos.services.maps.api import run_maps_api
from aos.services.maps.service import MapsService
from aos.services.maps.validation import (
    validate_get_seller_location_request,
    validate_map_points_request,
    validate_remove_seller_location_request,
    validate_set_seller_location_request,
)


def list_seller_map_points_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:maps:seller_points:ip:{request_ip()}",
        ttl_seconds=60,
        limit=LIST_SELLER_MAP_POINTS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many map requests. Please try again shortly.",
    )
    if limited:
        return limited

    def operation():
        request = validate_map_points_request(kwargs)
        return ok(
            "Seller map points fetched successfully.",
            data=MapsService().list_seller_map_points(request, viewer=current_user()),
        )

    return run_maps_api(
        operation,
        fallback="Failed to fetch seller map points.",
        log_title="AOS List Seller Map Points Failed",
        transactional=False,
    )


def get_seller_location_impl(**kwargs):
    def operation():
        request = validate_get_seller_location_request(kwargs)
        seller_reference = request["seller_reference"]
        if seller_reference:
            limited = rate_limit(
                key=f"aos:maps:seller_location:ip:{request_ip()}",
                ttl_seconds=60,
                limit=GET_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_IP,
                message="Too many seller-location requests. Please try again shortly.",
            )
            if limited:
                return limited
            viewer = current_user()
        else:
            viewer, error = require_login()
            if error:
                return error
            limited = rate_limit(
                key=rate_limit_key("maps", "get_my_seller_location", viewer),
                ttl_seconds=60,
                limit=GET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
                message="Too many seller-location requests. Please try again shortly.",
            )
            if limited:
                return limited
        return ok(
            "Seller location fetched successfully.",
            data=MapsService().get_seller_location(
                seller_reference=seller_reference,
                viewer=viewer,
            ),
        )

    return run_maps_api(
        operation,
        fallback="Failed to fetch seller location.",
        log_title="AOS Get Seller Location Failed",
        transactional=False,
    )


def set_my_seller_location_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=rate_limit_key("maps", "set_my_seller_location", user),
        ttl_seconds=60,
        limit=SET_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many location updates. Please try again shortly.",
    )
    if limited:
        return limited

    # Validation and reverse geocoding intentionally happen before the
    # mutation savepoint. Invalid requests and unavailable providers cannot
    # acquire or hold the Seller row lock.
    def operation():
        request = validate_set_seller_location_request(kwargs)
        service = MapsService()
        resolved = service.prepare_location_update(user=user, request=request)
        return ok(
            "Seller location saved successfully.",
            data=service.set_seller_location(
                user=user,
                request=request,
                resolved_location=resolved,
            ),
        )

    return run_maps_api(
        operation,
        fallback="Failed to save seller location.",
        log_title="AOS Set Seller Location Failed",
        transactional=True,
    )


def remove_my_seller_location_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=rate_limit_key("maps", "remove_my_seller_location", user),
        ttl_seconds=60,
        limit=REMOVE_MY_SELLER_LOCATION_LIMIT_PER_MINUTE_PER_USER,
        message="Too many location removal requests. Please try again shortly.",
    )
    if limited:
        return limited

    def operation():
        request = validate_remove_seller_location_request(kwargs)
        return ok(
            "Seller location removed successfully.",
            data=MapsService().remove_seller_location(user=user, request=request),
        )

    return run_maps_api(
        operation,
        fallback="Failed to remove seller location.",
        log_title="AOS Remove Seller Location Failed",
        transactional=True,
    )
