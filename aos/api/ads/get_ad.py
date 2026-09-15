"""Privacy-safe canonical public Ad detail."""
from __future__ import annotations
from typing import Any

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import GET_AD_FIELDS, MAX_AD_ATTRIBUTES, MAX_IMAGES
from aos.services.ads.errors import AdsNotFoundError
from aos.services.ads.validation import ensure_known_fields, normalize_identifier
from aos.services.analytics_pipeline_service import emit_analytics_event
from aos.services.currency_conversion import convert_amount
from aos.services.fx_service import get_fx_snapshot

from .activity import record_ad_view_activity
from .constants import GET_AD_LIMIT_PER_HOUR_PER_IP
from .media import project_ad_image_urls, project_ad_video_url
from .serializers import serialize_ad_detail


def _is_offer_active(ad_doc: Any, *, today: Any) -> bool:
    return bool(ad_doc.offer_price and (ad_doc.offer_start_date is None or ad_doc.offer_start_date <= today) and (ad_doc.offer_end_date is None or ad_doc.offer_end_date >= today))


def _apply_display_price(ad_doc: Any, *, requested_currency: str, today: Any) -> None:
    source=str(ad_doc.currency or "").strip(); snapshot=get_fx_snapshot()
    offer_active=_is_offer_active(ad_doc,today=today); native=ad_doc.offer_price if offer_active else ad_doc.price
    if source == requested_currency:
        original=convert_amount(ad_doc.price or 0,source,requested_currency,1,1,snapshot.base_currency)
        current=convert_amount(native or 0,source,requested_currency,1,1,snapshot.base_currency)
    elif snapshot.fresh:
        original=convert_amount(ad_doc.price or 0,source,requested_currency,snapshot.rates.get(source),snapshot.rates.get(requested_currency),snapshot.base_currency)
        current=convert_amount(native or 0,source,requested_currency,snapshot.rates.get(source),snapshot.rates.get(requested_currency),snapshot.base_currency)
    else:
        original=convert_amount(ad_doc.price or 0,source,requested_currency,None,None,snapshot.base_currency)
        current=convert_amount(native or 0,source,requested_currency,None,None,snapshot.base_currency)
    ad_doc.display_currency=original.display_currency
    ad_doc.requested_display_currency=requested_currency
    ad_doc.conversion_available=int(original.available)
    ad_doc.conversion_rate=original.rate
    ad_doc.original_price_converted=original.amount
    ad_doc.current_price=current.amount
    ad_doc.is_offer_active=offer_active


def _viewer_has_wishlisted(*, viewer: str, ad_name: str) -> bool:
    return viewer != "Guest" and bool(frappe.db.exists("AOS Wishlist", {"user":viewer,"ad":ad_name,"status":"Active"}))


def _record_view_best_effort(*, viewer: str, ad_name: str) -> None:
    if viewer == "Guest": return
    try: record_ad_view_activity(user=viewer, ad_id=ad_name)
    except Exception: frappe.log_error(frappe.get_traceback(), "AOS Ad View Activity Failed")


def get_ad_impl(**kwargs):
    limited=rate_limit(key=f"aos:ads:get:ip:{request_ip()}", ttl_seconds=3600, limit=GET_AD_LIMIT_PER_HOUR_PER_IP, message="Too many requests. Please try again later.")
    if limited: return limited

    def _get():
        ensure_known_fields(kwargs, GET_AD_FIELDS)
        public_id=normalize_identifier(kwargs.get("ad_id"), field="ad_id", required=True)
        _country, display_currency, market_error=resolve_market_context(country=kwargs.get("country"), currency=kwargs.get("currency"))
        if market_error: return market_error
        viewer=current_user(); today=getdate(nowdate())
        conditions = [
            "a.public_id = %(public_id)s",
            "a.status = 'Active'",
            "s.status = 'Active'",
            "u.enabled = 1",
            "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
        ]
        values={"public_id":public_id,"today":today}
        if viewer != "Guest":
            values["viewer"]=viewer
            conditions.append("""NOT EXISTS (SELECT 1 FROM `tabAOS User Block` b WHERE b.status='Active'
                AND ((b.blocker_user=%(viewer)s AND b.blocked_user=s.user) OR (b.blocker_user=s.user AND b.blocked_user=%(viewer)s)))""")
        row=frappe.db.sql(f"""SELECT a.name, loc.location AS location_name FROM `tabAOS Ad` a INNER JOIN `tabAOS Seller` s ON s.name=a.seller
            LEFT JOIN `tabAOS Location` loc ON loc.name=a.location
            INNER JOIN `tabAOS Profile` p ON p.user=s.user INNER JOIN `tabUser` u ON u.name=s.user
            WHERE {' AND '.join(conditions)} LIMIT 1""",values,as_dict=True)
        if not row: raise AdsNotFoundError("Ad not found.")
        ad_name=str(row[0].name); ad_doc=frappe.get_doc("AOS Ad",ad_name); ad_doc.location_name=str(row[0].location_name or "")
        ad_doc.images=list(ad_doc.images or [])[:MAX_IMAGES]; ad_doc.details=list(ad_doc.details or [])[:MAX_AD_ATTRIBUTES]
        project_ad_image_urls(ad_doc.images); project_ad_video_url(ad_doc)
        _apply_display_price(ad_doc,requested_currency=display_currency,today=today)
        item=serialize_ad_detail(ad_doc,is_wishlisted=_viewer_has_wishlisted(viewer=viewer,ad_name=ad_name))
        _record_view_best_effort(viewer=viewer,ad_name=ad_name)
        try: emit_analytics_event("ad_view", actor=viewer if viewer != "Guest" else None, target_type="AOS Ad", target_id=ad_name)
        except Exception: frappe.log_error(frappe.get_traceback(),"AOS Ad View Analytics Failed")
        return ok("Ad fetched.",data={"item":item})
    return run_ads_api(_get,fallback="Failed to fetch ad.",log_title="AOS Get Ad Failed")
