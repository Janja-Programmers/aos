"""Reviews endpoints.

Structure:
- Whitelisted wrappers here
- Business logic in sibling modules
"""

import frappe

from .create import create_review_impl
from .list import list_reviews_impl
from .toggle import toggle_reaction_impl
from .viewer_state import get_review_viewer_state_impl


@frappe.whitelist(methods=["POST"])
def create_review(**kwargs):
    """Create a review for an Ad (Pending approval)."""
    return create_review_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def list_reviews(**kwargs):
    """List approved reviews for an Ad."""
    return list_reviews_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_review_viewer_state(**kwargs):
    """Return current viewer's review eligibility/state for an Ad."""
    return get_review_viewer_state_impl(**kwargs)


@frappe.whitelist(methods=["POST"])
def toggle_reaction(**kwargs):
    """Like / Unlike / Dislike / Undislike a review."""
    return toggle_reaction_impl(**kwargs)
