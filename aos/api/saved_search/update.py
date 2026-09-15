from __future__ import annotations
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail,ok
from .api import run_saved_search_mutation
from .constants import UPDATE_SAVED_SEARCH_LIMIT_PER_MINUTE_PER_USER
from .service import update

def update_saved_search_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:saved-search:update:{user}",ttl_seconds=60,limit=UPDATE_SAVED_SEARCH_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _mutate():
        if set(kwargs)-{"search_id","title","query","version"}:return fail("Unknown request field.",error="SEARCH_INVALID_FILTERS",http_status=422)
        doc=update(user=user,public_id=kwargs.get("search_id"),title=kwargs.get("title"),query=kwargs.get("query"),version=kwargs.get("version"))
        return ok("Saved search updated.",data={"id":doc.public_id,"version":str(doc.modified),"query":doc.params_json})
    return run_saved_search_mutation(_mutate,fallback="Failed to update saved search.",log_title="AOS Update Saved Search Failed")
