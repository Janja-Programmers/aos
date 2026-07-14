"""Public AOS API v1 wrappers for saved_search.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.saved_search.*.
Implementation stays in aos.api.saved_search implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.saved_search.save import (
    save_search_impl as _save_search_impl,
)
from aos.api.saved_search.list import (
    list_saved_searches_impl as _list_saved_searches_impl,
)
from aos.api.saved_search.delete import (
    delete_saved_search_impl as _delete_saved_search_impl,
)

@frappe.whitelist(methods=["POST"])
def save_search(**kwargs):
    """Execute the v1 saved_search.save_search endpoint."""
    return _save_search_impl(**kwargs)


@frappe.whitelist()
def list_saved_searches(**kwargs):
    """Execute the v1 saved_search.list_saved_searches endpoint."""
    return _list_saved_searches_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def delete_saved_search(**kwargs):
    """Execute the v1 saved_search.delete_saved_search endpoint."""
    return _delete_saved_search_impl(**kwargs)
