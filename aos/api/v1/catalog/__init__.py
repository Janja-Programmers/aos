"""Stable public AOS API v1 wrappers for Catalog."""

from __future__ import annotations

import frappe

from aos.api.shared.transport import execute_endpoint as _execute_endpoint
from aos.api.catalog.categories import get_categories_impl as _get_categories_impl
from aos.api.catalog.options import get_attribute_options_impl as _get_attribute_options_impl
from aos.api.catalog.schema import get_category_schema_impl as _get_category_schema_impl


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_categories(**kwargs):
    """Return the active public category tree."""
    return _execute_endpoint(_get_categories_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_category_schema(**kwargs):
    """Return the resolved public schema for one category."""
    return _execute_endpoint(_get_category_schema_impl, kwargs)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_attribute_options(**kwargs):
    """Return valid options for one category attribute and parent selection."""
    return _execute_endpoint(_get_attribute_options_impl, kwargs)
