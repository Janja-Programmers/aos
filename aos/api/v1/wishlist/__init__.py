"""Canonical public AOS API v1 Wishlist endpoints."""

from __future__ import annotations

import frappe

from aos.api.v1._transport import client_kwargs as _client_kwargs
from aos.api.wishlist.add import add_to_wishlist_impl as _add_to_wishlist_impl
from aos.api.wishlist.list import list_wishlist_impl as _list_wishlist_impl
from aos.api.wishlist.remove import remove_from_wishlist_impl as _remove_from_wishlist_impl


@frappe.whitelist(methods=["POST"])
def add_to_wishlist(**kwargs):
    """Converge the current user's Wishlist state to saved for one public Ad ID."""
    return _add_to_wishlist_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["POST"])
def remove_from_wishlist(**kwargs):
    """Converge the current user's Wishlist state to not saved for one public Ad ID."""
    return _remove_from_wishlist_impl(**_client_kwargs(kwargs))


@frappe.whitelist(methods=["GET"])
def list_wishlist(**kwargs):
    """List the current user's publicly viewable Wishlist Ads."""
    return _list_wishlist_impl(**_client_kwargs(kwargs))
