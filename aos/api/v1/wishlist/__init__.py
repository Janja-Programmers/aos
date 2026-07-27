"""Public AOS API v1 wrappers for wishlist.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.wishlist.*.
Implementation stays in aos.api.wishlist implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.v1._transport import client_kwargs as _client_kwargs

from aos.api.wishlist.toggle import (
    toggle_wishlist_impl as _toggle_wishlist_impl,
)
from aos.api.wishlist.list import (
    list_wishlist_impl as _list_wishlist_impl,
)

@frappe.whitelist(methods=["POST"])
def toggle_wishlist(**kwargs):
    """Add or remove an Ad from the current user's wishlist."""
    return _toggle_wishlist_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def list_wishlist(**kwargs):
    """List current user's wishlist (Active only)."""
    return _list_wishlist_impl(**_client_kwargs(kwargs))
