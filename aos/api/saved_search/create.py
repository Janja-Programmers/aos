from __future__ import annotations
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail,ok
from .api import run_saved_search_mutation
from .constants import CREATE_SAVED_SEARCH_LIMIT_PER_MINUTE_PER_USER
from .service import create

def create_saved_search_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:saved-search:create:{user}",ttl_seconds=60,limit=CREATE_SAVED_SEARCH_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _mutate():
        unknown=set(kwargs)-{"title","query"}
        if unknown: return fail("Unknown request field.",error="SEARCH_INVALID_FILTERS",http_status=422)
        doc=create(user=user,title=kwargs.get("title"),query=kwargs.get("query"))
        return ok("Search saved.",data={"id":doc.public_id,"version":str(doc.modified),"query":doc.params_json})
    return run_saved_search_mutation(_mutate,fallback="Failed to save search.",log_title="AOS Create Saved Search Failed")
