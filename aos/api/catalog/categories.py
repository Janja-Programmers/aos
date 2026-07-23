"""Public category browsing implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.catalog.errors import CatalogError, public_catalog_message
from aos.services.catalog.observability import catalog_log
from aos.services.catalog.service import CatalogService

from .constants import GET_CATEGORIES_LIMIT_PER_HOUR_PER_IP


def get_categories_impl(**_kwargs):
    """Return the active public category tree with deterministic ordering."""

    limited = rate_limit(
        key=rate_limit_key("catalog", "categories", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=GET_CATEGORIES_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if limited:
        return limited

    try:
        tree = CatalogService().list_public_categories()
        catalog_log("categories_read", outcome="success")
        return ok("Categories fetched.", data=tree)
    except CatalogError as exc:
        catalog_log("categories_read", outcome="failure")
        if exc.http_status >= 500:
            frappe.log_error(frappe.get_traceback(), "AOS Catalog Categories Data Failure")
        message = (
            "Failed to fetch categories."
            if exc.http_status >= 500
            else public_catalog_message(exc)
        )
        return fail(message, error=exc.code, http_status=exc.http_status)
    except Exception:
        catalog_log("categories_read", outcome="failure")
        frappe.log_error(frappe.get_traceback(), "AOS Get Categories Failed")
        return fail("Failed to fetch categories.", error="INTERNAL_ERROR")
