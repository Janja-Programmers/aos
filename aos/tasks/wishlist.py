"""Best-effort background side effects for Wishlist changes."""

from __future__ import annotations

import frappe


def sync_wishlist_activity(*, user: str, ad_id: str) -> None:
    """Converge Activity history on the current authoritative Wishlist state.

    Querying current state makes rapidly queued add/remove jobs safe even when
    workers execute them out of order.
    """
    try:
        from aos.api.ads.activity import hide_ad_wishlist_activity, record_ad_wishlist_activity

        active = bool(
            frappe.db.exists(
                "AOS Wishlist",
                {"user": user, "ad": ad_id, "status": "Active"},
            )
        )
        if active:
            record_ad_wishlist_activity(user=user, ad_id=ad_id)
        else:
            hide_ad_wishlist_activity(user=user, ad_id=ad_id)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Wishlist Activity Side Effect Failed")
