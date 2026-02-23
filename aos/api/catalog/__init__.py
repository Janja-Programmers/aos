"""Catalog endpoints.

This feature module will host Catalog-related APIs such as Categories, Listings,
Listing Media, etc.

Pattern (consistent with aos.api.auth and aos.api.accounts):
  - whitelisted wrappers live in this __init__.py
  - business logic lives in sibling modules
"""

from __future__ import annotations

import frappe

from .categories import get_categories_impl
from .schema import get_category_schema_impl


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_categories(include_inactive: int | None = None):
    """Return AOS categories.

    Args:
        include_inactive: optional flag (0/1). Defaults to 0.
            - 0: return only active categories (default for mobile)
            - 1: include inactive categories (useful for admin/testing)
    """

    return get_categories_impl(include_inactive=bool(int(include_inactive or 0)))


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_category_schema(category: str):
    """Return resolved schema (details + pricing) for an Ad category."""

    return get_category_schema_impl(category)