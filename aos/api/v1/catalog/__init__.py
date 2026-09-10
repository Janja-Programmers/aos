"""Stable public AOS API v1 wrappers for Catalog."""

from __future__ import annotations

import frappe

from aos.api.catalog.categories import get_categories_impl as _get_categories_impl
from aos.api.catalog.schema import get_category_schema_impl as _get_category_schema_impl


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_categories(**kwargs):
    """Return the active public category tree."""
    return _get_categories_impl(**kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_category_schema(**kwargs):
    """Return the resolved public schema for one category."""
    return _get_category_schema_impl(**kwargs)
