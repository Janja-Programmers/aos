"""Seller-owned Ad detail for management/editing."""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.authorization import get_owned_ad_row
from aos.services.ads.constants import GET_MY_AD_FIELDS
from aos.services.ads.validation import ensure_known_fields, normalize_identifier

from .constants import GET_MY_AD_LIMIT_PER_MINUTE_PER_USER
from .serializers import serialize_ad_for_edit


def get_my_ad_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:ads:get_my:user:{user}",
        ttl_seconds=60,
        limit=GET_MY_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _get():
        ensure_known_fields(kwargs, GET_MY_AD_FIELDS)
        ad_id = normalize_identifier(kwargs.get("ad_id") or kwargs.get("id"), field="ad_id", required=True)
        get_owned_ad_row(user, ad_id)
        return ok("Ad fetched.", data={"item": serialize_ad_for_edit(frappe.get_doc("AOS Ad", ad_id))})

    return run_ads_api(_get, fallback="Failed to fetch ad.", log_title="AOS Get My Ad Failed")
