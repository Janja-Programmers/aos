"""Public Seller map-point API backed by Maps geospatial primitives."""
from __future__ import annotations
from aos.api.shared.auth import current_user
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.maps.service import MapsService
from aos.services.maps.validation import validate_map_points_request
from aos.services.sellers.api import run_seller_api
from aos.services.sellers.constants import RATE_LIMITS


def list_seller_map_points_impl(**kwargs):
    limited = rate_limit(key=rate_limit_key("sellers", "map_points_ip", request_ip()), ttl_seconds=60, limit=RATE_LIMITS["map_points_ip"], message="Too many map requests. Please try again shortly.")
    if limited:
        return limited
    def operation():
        request = validate_map_points_request(kwargs)
        viewer = current_user()
        viewer = viewer if viewer and viewer != "Guest" else None
        return ok("Seller map points fetched successfully.", data=MapsService().list_seller_map_points(request, viewer=viewer))
    return run_seller_api(operation, fallback="Failed to fetch seller map points.", log_title="AOS List Seller Map Points Failed", transactional=False, operation_name="map_points")
