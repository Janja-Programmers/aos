"""Public AOS API v1 wrappers for maps.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.maps.*.
Implementation stays in aos.api.maps implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.v1._transport import client_kwargs as _client_kwargs

from aos.api.maps.autocomplete import (
    autocomplete_places_impl as _autocomplete_places_impl,
)
from aos.api.maps.search import (
    search_places_impl as _search_places_impl,
)
from aos.api.maps.reverse import (
    reverse_geocode_impl as _reverse_geocode_impl,
)
from aos.api.maps.route import (
    get_route_impl as _get_route_impl,
    refresh_route_impl as _refresh_route_impl,
)

@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def autocomplete_places(**kwargs):
    """Fast global place autocomplete with optional location bias."""
    return _autocomplete_places_impl(**_client_kwargs(kwargs))


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def search_places(**kwargs):
    """Search places globally through the provider-neutral AOS contract."""
    return _search_places_impl(**_client_kwargs(kwargs))


@frappe.whitelist(
    allow_guest=True,
    methods=["GET"],
)
def reverse_geocode(**kwargs):
    """Resolve coordinates into a normalized address."""
    return _reverse_geocode_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def get_route(**kwargs):
    """Calculate a route between two or more WGS84 locations."""
    return _get_route_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def refresh_route(**kwargs):
    """Refresh a route from the buyer's current location to a seller."""
    return _refresh_route_impl(**_client_kwargs(kwargs))
