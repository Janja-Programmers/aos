"""Public AOS API v1 wrappers for catalog.

These thin wrappers are the stable external contract for /api/method/aos.api.v1.catalog.*.
Implementation stays in aos.api.catalog implementation modules.
"""

from __future__ import annotations

import frappe

from aos.api.catalog.categories import (
    get_categories_impl as _get_categories_impl,
)
from aos.api.catalog.schema import (
    get_category_schema_impl as _get_category_schema_impl,
)

@frappe.whitelist(allow_guest=True)
def get_categories(**kwargs):
    """Execute the v1 catalog.get_categories endpoint."""
    return _get_categories_impl(**kwargs)


@frappe.whitelist(allow_guest=True)
def get_category_schema(**kwargs):
    """Execute the v1 catalog.get_category_schema endpoint."""
    return _get_category_schema_impl(**kwargs)
