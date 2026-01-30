"""Attributes + schema endpoints.

This feature module powers the dynamic "Details" and "Pricing" steps when
posting an Ad (Jiji-style).

Pattern (consistent with other aos.api.* features):
  - whitelisted wrappers live in this __init__.py
  - business logic lives in sibling modules
"""

from __future__ import annotations

import frappe

from .schema import get_category_schema_impl


@frappe.whitelist(allow_guest=True, methods=["GET"])
def get_category_schema(category: str):
    """Return resolved schema (details + pricing) for an Ad category."""

    return get_category_schema_impl(category)
