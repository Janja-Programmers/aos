"""
Maps endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

from __future__ import annotations

import frappe

from .autocomplete import autocomplete_places_impl
from .reverse import reverse_geocode_impl
from .route import get_route_impl, refresh_route_impl
from .search import search_places_impl


@frappe.whitelist(
    allow_guest=True,
    methods=["GET", "POST"],
)
def autocomplete_places(**kwargs):
    """Fast place autocomplete within the supported AOS map area."""
    return autocomplete_places_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET", "POST"],
)
def search_places(**kwargs):
    """Search places within the supported AOS map area."""
    return search_places_impl(**kwargs)


@frappe.whitelist(
    allow_guest=True,
    methods=["GET", "POST"],
)
def reverse_geocode(**kwargs):
    """Resolve coordinates into a normalized address."""
    return reverse_geocode_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def get_route(**kwargs):
    """Calculate a route between two or more supported locations."""
    return get_route_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def refresh_route(**kwargs):
    """Refresh a route from the buyer's current location to a seller."""
    return refresh_route_impl(**kwargs)
