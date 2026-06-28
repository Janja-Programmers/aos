"""Sellers endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

from __future__ import annotations

import frappe

from .get_location import get_seller_location_impl
from .get_my_seller_status import (
    get_my_seller_status_impl,
)
from .get_seller import get_seller_impl
from .list_sellers import list_sellers_impl
from .map_points import list_seller_map_points_impl
from .remove_location import (
    remove_my_seller_location_impl,
)
from .set_location import (
    set_my_seller_location_impl,
)
from .update_my_seller import (
    update_my_seller_impl,
)


@frappe.whitelist(allow_guest=True)
def list_sellers(**kwargs):
    """List marketplace sellers."""
    return list_sellers_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_seller(**kwargs):
    """Get seller profile for storefront and ad detail."""
    return get_seller_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET", "POST"],
)
def list_seller_map_points(**kwargs):
    """List seller pins or clusters for a map viewport."""
    return list_seller_map_points_impl(**kwargs)


@frappe.whitelist()
def get_my_seller_status(**kwargs):
    """Get the current user's seller status for UI decisions."""
    return get_my_seller_status_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def update_my_seller(**kwargs):
    """Update the authenticated seller's general profile."""
    return update_my_seller_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def set_my_seller_location(**kwargs):
    """Set or replace the authenticated seller's map location."""
    return set_my_seller_location_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def remove_my_seller_location(**kwargs):
    """Remove the authenticated seller's saved map location."""
    return remove_my_seller_location_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET", "POST"],
)
def get_seller_location(**kwargs):
    """
    Get a seller location.

    When seller is omitted, login is required and the authenticated
    user's seller location is returned.

    When seller is supplied, the active seller's public location is
    returned.
    """
    return get_seller_location_impl(**kwargs)
