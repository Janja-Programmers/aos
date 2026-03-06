"""Catalog endpoints.
  - whitelisted wrappers live in this __init__.py
  - business logic lives in sibling modules
"""

from __future__ import annotations

import frappe

from .categories import get_categories_impl
from .schema import get_category_schema_impl


@frappe.whitelist(allow_guest=True)
def get_categories(**kwargs):
    return get_categories_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_category_schema(**kwargs):
    return get_category_schema_impl(**kwargs)

