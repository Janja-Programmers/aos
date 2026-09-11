"""Public dependent attribute option resolution."""

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

from .constants import GET_ATTRIBUTE_OPTIONS_LIMIT_PER_HOUR_PER_IP


def get_attribute_options_impl(**kwargs):
    """Return valid options for one category attribute and parent selection."""

    limited = rate_limit(
        key=rate_limit_key("catalog", "attribute_options", "ip", request_ip()),
        ttl_seconds=60 * 60,
        limit=GET_ATTRIBUTE_OPTIONS_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if limited:
        return limited

    try:
        reject_unknown_fields(kwargs, allowed={"category", "attribute", "parent_value"})
        if kwargs.get("category") in (None, "") or kwargs.get("attribute") in (None, ""):
            raise CatalogValidationError(
                "Category and attribute are required.", code="INVALID_CATALOG_INPUT"
            )
        data = CatalogService().get_public_attribute_options(
            category=kwargs["category"],
            attribute=kwargs["attribute"],
            parent_value=kwargs.get("parent_value"),
        )
        catalog_log("attribute_options_read", outcome="success")
        return ok("Attribute options fetched.", data=data)
    except CatalogNotFoundError as exc:
        catalog_log("attribute_options_read", outcome="not_found")
        return fail("Category not found.", error=exc.code, http_status=exc.http_status)
    except CatalogError as exc:
        catalog_log(
            "attribute_options_read",
            outcome="rejected" if exc.http_status < 500 else "failure",
        )
        if exc.http_status >= 500:
            frappe.log_error(frappe.get_traceback(), "AOS Catalog Attribute Options Data Failure")
        message = (
            "Failed to fetch attribute options."
            if exc.http_status >= 500
            else public_catalog_message(exc)
        )
        return fail(message, error=exc.code, http_status=exc.http_status)
    except Exception:
        catalog_log("attribute_options_read", outcome="failure")
        frappe.log_error(frappe.get_traceback(), "AOS Get Attribute Options Failed")
        return fail("Failed to fetch attribute options.", error="INTERNAL_ERROR")
