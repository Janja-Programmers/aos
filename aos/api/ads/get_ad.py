"""Get a single Ad by id."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import current_user
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import GET_AD_LIMIT_PER_HOUR_PER_IP
from .serializers import serialize_ad_detail


def get_ad_impl(ad_id: Any):
    rl = rate_limit(
        key=f"aos:ads:get:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GET_AD_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    ad_id = str(ad_id or "").strip()
    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    try:
        doc = frappe.get_doc("AOS Ad", ad_id)
    except frappe.DoesNotExistError:
        return fail("Ad not found.", code="NOT_FOUND")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Ad Failed")
        return fail("Failed to fetch ad.", code="INTERNAL_ERROR")

    # Only Active ads visible to buyers
    if str(getattr(doc, "status", "") or "").strip() != "Active":
        return fail("Ad not found.", code="NOT_FOUND")

    # Fetch wishlist
    user = current_user()
    wishlisted_ids = get_active_wishlist_ad_ids(user)

    item = serialize_ad_detail(
        doc,
        is_wishlisted=doc.name in wishlisted_ids,
    )

    return ok("Ad fetched.", data={"item": item})
