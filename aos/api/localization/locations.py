from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.market_context import resolve_market_country

from .constants import GET_LOCATIONS_LIMIT_PER_MINUTE_PER_IP

def get_locations_impl(country: str | None = None):
    """
    Return active locations for the resolved market country.
    Market country is strictly enforced.
    """

    # Rate limit per IP (guest allowed)
    rl = rate_limit(
        key=f"aos:locations:list:ip:{request_ip()}",
        ttl_seconds=60,
        limit=GET_LOCATIONS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    # Resolve Market Country (Strictly enforced)
    country_name, error = resolve_market_country(country)
    if error:
        return error

    # Fetch Locations
    locations = frappe.get_all(
        "AOS Location",
        filters={
            "country": country_name,
            "is_active": 1,
        },
        fields=[
            "name",
            "location_name",
            "country",
            "sort_order",
            "is_active",
        ],
        order_by="sort_order asc, location_name asc",
    )

    return ok(
        "Locations fetched.",
        data=[
            {
                "id": location.name,
                "name": location.location_name,
                "country": location.country,
                "sort_order": location.sort_order or 0,
                "is_active": location.is_active,
            }
            for location in locations
        ],
    )
