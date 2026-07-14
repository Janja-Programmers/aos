"""Public AOS API v1 wrappers for search_ranking.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.search_ranking.*.
Implementation stays in aos.api.search_ranking implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.search_ranking.callback import (
    handle_callback_impl as _handle_callback_impl,
)
from aos.api.search_ranking.recommendations import (
    related_ads_impl as _related_ads_impl,
)

@frappe.whitelist(allow_guest=True, methods=["POST"])
def handle_callback(**kwargs):
    """Execute the v1 search_ranking.handle_callback endpoint."""
    return _handle_callback_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def related_ads(**kwargs):
    """Execute the v1 search_ranking.related_ads endpoint."""
    return _related_ads_impl(**kwargs)
