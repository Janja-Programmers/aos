"""Owner Ad detail using opaque public IDs."""
from __future__ import annotations
import frappe
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.authorization import get_owned_ad_row
from aos.services.ads.constants import GET_MY_AD_FIELDS
from aos.services.ads.validation import ensure_known_fields, normalize_identifier
from aos.services.marketplace_discovery.ids import resolve_ad_name
from .constants import GET_MY_AD_LIMIT_PER_MINUTE_PER_USER
from .media import project_ad_image_urls, project_ad_video_url
from .serializers import serialize_ad_for_edit

def get_my_ad_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:ads:get_my:user:{user}",ttl_seconds=60,limit=GET_MY_AD_LIMIT_PER_MINUTE_PER_USER,message="Too many requests. Please try again shortly.")
    if limited:return limited
    def _get():
        ensure_known_fields(kwargs,GET_MY_AD_FIELDS)
        public_id=normalize_identifier(kwargs.get("ad_id"),field="ad_id",required=True)
        ad_name=resolve_ad_name(public_id); get_owned_ad_row(user,ad_name)
        ad_doc=frappe.get_doc("AOS Ad",ad_name); project_ad_image_urls(list(ad_doc.images or [])); project_ad_video_url(ad_doc)
        return ok("Ad fetched.",data={"item":serialize_ad_for_edit(ad_doc)})
    return run_ads_api(_get,fallback="Failed to fetch ad.",log_title="AOS Get My Ad Failed")
