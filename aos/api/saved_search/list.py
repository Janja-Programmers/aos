from __future__ import annotations
import frappe
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail,ok
from aos.services.ads.api import ads_fail
from aos.services.ads.errors import AdsError
from .constants import LIST_SEARCH_LIMIT_PER_MINUTE_PER_USER
from .service import list_owned

def list_saved_searches_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:saved-search:list:{user}",ttl_seconds=60,limit=LIST_SEARCH_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    try:
        if set(kwargs)-{"limit","cursor"}:return fail("Unknown request field.",error="SEARCH_INVALID_FILTERS",http_status=422)
        items,cursor=list_owned(user=user,limit=kwargs.get("limit"),cursor=kwargs.get("cursor"))
        return ok("Saved searches fetched.",data={"items":items,"next_cursor":cursor})
    except AdsError as exc:return ads_fail(exc,fallback="Failed to fetch saved searches.")
    except Exception:
        frappe.log_error(frappe.get_traceback(),"AOS List Saved Search Failed");return fail("Failed to fetch saved searches.",error="INTERNAL_ERROR",http_status=500)
