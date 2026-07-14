"""Public AOS API v1 wrappers for reviews.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.reviews.*.
Implementation stays in aos.api.reviews implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.reviews.create import (
    create_review_impl as _create_review_impl,
)
from aos.api.reviews.list import (
    list_reviews_impl as _list_reviews_impl,
)
from aos.api.reviews.viewer_state import (
    get_review_viewer_state_impl as _get_review_viewer_state_impl,
)
from aos.api.reviews.toggle import (
    toggle_reaction_impl as _toggle_reaction_impl,
)

@frappe.whitelist(methods=["POST"])
def create_review(**kwargs):
    """Create a review for an Ad (Pending approval)."""
    return _create_review_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_reviews(**kwargs):
    """List approved reviews for an Ad."""
    return _list_reviews_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_review_viewer_state(**kwargs):
    """Return current viewer's review eligibility/state for an Ad."""
    return _get_review_viewer_state_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_reaction(**kwargs):
    """Like / Unlike / Dislike / Undislike a review."""
    return _toggle_reaction_impl(**kwargs)
