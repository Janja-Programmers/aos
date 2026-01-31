"""Locations endpoints.

DocType: AOS Location

Used by mobile clients when posting an ad:
- Country is selected / defaulted from user preferences
- Location dropdown should show only locations for that country
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import GET_LOCATIONS_LIMIT_PER_MIN_PER_IP
from .validators import resolve_country


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _serialize_location(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("name"),
        "name": row.get("location_name") or row.get("name"),
        "country": row.get("country"),
        "sort_order": _safe_int(row.get("sort_order"), 0),
        "is_active": _safe_int(row.get("is_active"), 0),
    }


def get_locations_impl(country: str | None = None, include_inactive: bool = False):
    """Return locations for a country.

    Args:
        country: Country.name or Country.code.
        include_inactive: optional flag (0/1). Defaults to False.
    """

    rl = rate_limit(
        key=f"aos:locale:locations:ip:{request_ip()}",
        ttl_seconds=60,
        limit=GET_LOCATIONS_LIMIT_PER_MIN_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    country_name, err = resolve_country(country)
    if err:
        return err
    if not country_name:
        return fail("Country is required.", code="VALIDATION_ERROR", data={"field": "country"})

    try:
        filters: Dict[str, Any] = {"country": country_name}
        if not include_inactive:
            filters["is_active"] = 1

        rows = frappe.get_all(
            "AOS Location",
            filters=filters,
            fields=["name", "location_name", "country", "sort_order", "is_active"],
            order_by="sort_order asc, location_name asc",
            limit_page_length=5000,
        )

        data = [_serialize_location(r) for r in (rows or [])]
        return ok("Locations fetched.", data=data)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Locations Failed")
        return fail("Failed to fetch locations.", code="INTERNAL_ERROR")
