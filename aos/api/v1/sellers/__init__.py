"""Public AOS API v1 wrappers for sellers.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.sellers.*.
Implementation stays in aos.api.sellers implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.shared.transport import execute_endpoint as _execute_endpoint

from aos.api.sellers.list_sellers import (
    list_sellers_impl as _list_sellers_impl,
)
from aos.api.sellers.get_seller import (
    get_seller_impl as _get_seller_impl,
)
from aos.api.sellers.map_points import (
    list_seller_map_points_impl as _list_seller_map_points_impl,
)
from aos.api.sellers.get_my_seller_status import (
    get_my_seller_status_impl as _get_my_seller_status_impl,
)
from aos.api.sellers.update_my_seller import (
    update_my_seller_impl as _update_my_seller_impl,
)
from aos.api.sellers.set_location import (
    set_my_seller_location_impl as _set_my_seller_location_impl,
)
from aos.api.sellers.remove_location import (
    remove_my_seller_location_impl as _remove_my_seller_location_impl,
)
from aos.api.sellers.get_location import (
    get_seller_location_impl as _get_seller_location_impl,
)

@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_sellers(**kwargs):
    """List marketplace sellers."""
    return _execute_endpoint(_list_sellers_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_seller(**kwargs):
    """Get seller profile for storefront and ad detail."""
    return _execute_endpoint(_get_seller_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def list_seller_map_points(**kwargs):
    """List seller pins or clusters for a map viewport."""
    return _execute_endpoint(_list_seller_map_points_impl, kwargs)


@frappe.whitelist(methods=["GET"])
def get_my_seller_status(**kwargs):
    """Get the current user's seller status for UI decisions."""
    return _execute_endpoint(_get_my_seller_status_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_seller(**kwargs):
    """Update the authenticated seller's general profile."""
    return _execute_endpoint(_update_my_seller_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def set_my_seller_location(**kwargs):
    """Set or replace the authenticated seller's map location."""
    return _execute_endpoint(_set_my_seller_location_impl, kwargs)


@frappe.whitelist(methods=["POST"])
def remove_my_seller_location(**kwargs):
    """Remove the authenticated seller's saved map location."""
    return _execute_endpoint(_remove_my_seller_location_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_seller_location(**kwargs):
    """Get a seller location."""
    return _execute_endpoint(_get_seller_location_impl, kwargs)
