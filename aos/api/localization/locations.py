from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from .constants import GET_LOCATIONS_LIMIT_PER_MINUTE_PER_IP
from .context import resolve_effective_preference_context
from aos.services.localization_service import serialize_country


def _limit(value) -> int:
    try:
        return min(max(int(value or 20), 1), 100)
    except (TypeError, ValueError):
        return 20


def get_locations_impl(**kwargs):
    limited = rate_limit(key=f"aos:locations:list:ip:{request_ip()}", ttl_seconds=60, limit=GET_LOCATIONS_LIMIT_PER_MINUTE_PER_IP, message="Too many requests. Please try again shortly.")
    if limited:
        return limited
    try:
        context, error = resolve_effective_preference_context(country=kwargs.get("country"))
        country = context.get("country") if context else None
        if error:
            return error
        q = str(kwargs.get("q") or kwargs.get("search") or "").strip()
        filters = {"country": country, "is_active": 1}
        if q:
            filters["location"] = ["like", f"%{q}%"]
        rows = frappe.get_all("AOS Location", filters=filters, fields=["name", "location", "country", "sort_order"], order_by="sort_order asc, location asc, name asc", limit_page_length=_limit(kwargs.get("limit")))
        return ok("Locations fetched.", data={"country": serialize_country(country), "locations": [{"id": row.name, "name": row.location, "country": row.country, "sort_order": row.sort_order or 0} for row in rows]})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Locations Failed")
        return fail("Failed to fetch locations.", error="INTERNAL_ERROR")
