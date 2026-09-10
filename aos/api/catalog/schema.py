"""Public category schema implementation."""

from __future__ import annotations

import frappe

from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.services.catalog.errors import (
    CatalogError,
    CatalogNotFoundError,
    CatalogValidationError,
    public_catalog_message,
)
from aos.services.catalog.observability import catalog_log
from aos.services.catalog.service import CatalogService
from aos.services.catalog.validation import reject_unknown_fields

from .constants import GET_CATEGORY_SCHEMA_LIMIT_PER_HOUR_PER_IP


def get_category_schema_impl(**kwargs):
    """Return the resolved public attribute and pricing schema for one category."""

    limited = rate_limit(
        key=rate_limit_key("catalog", "schema", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=GET_CATEGORY_SCHEMA_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if limited:
        return limited

    try:
        reject_unknown_fields(kwargs, allowed={"category"})
        if kwargs.get("category") in (None, ""):
            raise CatalogValidationError("Category is required.", code="INVALID_CATALOG_INPUT")

        data = CatalogService().get_public_schema(kwargs["category"])
        category_kind = "group" if int(data["category"].get("is_group") or 0) else "leaf"
        catalog_log("schema_read", outcome="success", category_kind=category_kind)
        return ok("Category schema fetched.", data=data)
    except CatalogNotFoundError as exc:
        catalog_log("schema_read", outcome="not_found")
        return fail("Category not found.", error=exc.code, http_status=exc.http_status)
    except CatalogError as exc:
        catalog_log("schema_read", outcome="rejected" if exc.http_status < 500 else "failure")
        if exc.http_status >= 500:
            frappe.log_error(frappe.get_traceback(), "AOS Catalog Schema Data Failure")
        message = "Failed to fetch category schema." if exc.http_status >= 500 else public_catalog_message(exc)
        return fail(message, error=exc.code, http_status=exc.http_status)
    except Exception:
        catalog_log("schema_read", outcome="failure")
        frappe.log_error(frappe.get_traceback(), "AOS Get Category Schema Failed")
        return fail("Failed to fetch category schema.", error="INTERNAL_ERROR")
