"""Public category schema endpoint and compatibility helpers.

The compatibility helpers remain importable because Ads validation and
serialization historically consumed them. Their implementation now delegates
to the centralized Catalog service and uses bounded bulk queries.
"""

from __future__ import annotations

from typing import Any

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
from aos.services.catalog.service import (
    CatalogService,
    attribute_key,
    resolve_attributes,
    resolve_pricing,
)
from aos.services.catalog.validation import split_choices

from .constants import GET_CATEGORY_SCHEMA_LIMIT_PER_HOUR_PER_IP


def _split_lines(value: Any) -> list[str]:
    return split_choices(value, field="catalog_option")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _attribute_key(attribute_name: str) -> str:
    return attribute_key(attribute_name)


def _get_category_chain(category: str) -> list[dict[str, Any]]:
    """Compatibility boundary used by Ads; raises on corrupt Catalog data."""

    try:
        return CatalogService().get_category_chain(category, require_active=False)
    except CatalogNotFoundError:
        return []


def _resolve_pricing(chain_leaf_to_root: list[dict[str, Any]]) -> dict[str, Any]:
    return resolve_pricing(chain_leaf_to_root)


def _resolve_attributes(chain_leaf_to_root: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return resolve_attributes(chain_leaf_to_root)


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

    if kwargs.get("category") in (None, ""):
        return fail("Category is required.", error="VALIDATION_ERROR")

    try:
        data = CatalogService().get_public_schema(kwargs.get("category"))
        category_kind = "group" if int(data["category"].get("is_group") or 0) else "leaf"
        catalog_log("schema_read", outcome="success", category_kind=category_kind)
        return ok("Category schema fetched.", data=data)
    except CatalogNotFoundError as exc:
        catalog_log("schema_read", outcome="not_found")
        return fail("Category not found.", error=exc.code, http_status=exc.http_status)
    except CatalogValidationError as exc:
        catalog_log("schema_read", outcome="rejected")
        return fail(public_catalog_message(exc), error=exc.code, http_status=exc.http_status)
    except CatalogError as exc:
        catalog_log("schema_read", outcome="failure")
        if exc.http_status >= 500:
            frappe.log_error(frappe.get_traceback(), "AOS Catalog Schema Data Failure")
        message = (
            "Failed to fetch category schema."
            if exc.http_status >= 500
            else public_catalog_message(exc)
        )
        return fail(message, error=exc.code, http_status=exc.http_status)
    except Exception:
        catalog_log("schema_read", outcome="failure")
        frappe.log_error(frappe.get_traceback(), "AOS Get Category Schema Failed")
        return fail("Failed to fetch category schema.", error="INTERNAL_ERROR")
