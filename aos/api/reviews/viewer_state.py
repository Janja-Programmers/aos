"""Review viewer state endpoint."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import optional_active_user
from aos.api.shared.responses import fail, ok

from .eligibility import get_review_eligibility_for_ad


def get_review_viewer_state_impl(**kwargs):
    """
    Return current viewer's review state for an Ad.

    Frontend can use this to display:
    - Write Review
    - Contact seller to review
    - Already reviewed
    - Login to review
    """

    ad = str(kwargs.get("ad") or "").strip()

    if not ad:
        return fail("Ad is required.", error="VALIDATION_ERROR")

    ad_doc = frappe.db.get_value(
        "AOS Ad",
        ad,
        ["name", "seller", "status"],
        as_dict=True,
    )

    if not ad_doc or ad_doc.status != "Active":
        return fail("Ad not found.", error="NOT_FOUND")

    current_user = optional_active_user()

    state = get_review_eligibility_for_ad(
        ad_doc=ad_doc,
        current_user=current_user,
    )

    return ok(
        "Review viewer state fetched.",
        data=state,
    )
