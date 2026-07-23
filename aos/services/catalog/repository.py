"""Bounded Catalog persistence queries."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import MAX_CATEGORIES, MAX_CATEGORY_ATTRIBUTES
from .errors import CatalogDataError

_CATEGORY_FIELDS = [
    "name",
    "category_name",
    "icon",
    "icon_media",
    "parent_aos_category",
    "sort_order",
    "is_group",
    "is_active",
    "is_service",
    "pricing_requirement",
    "allowed_price_types",
    "allowed_price_units",
]


class CatalogRepository:
    """All Catalog reads are bounded and select only reviewed fields."""

    def load_categories(self) -> list[dict[str, Any]]:
        rows = frappe.get_all(
            "AOS Category",
            fields=_CATEGORY_FIELDS,
            order_by="sort_order asc, category_name asc, name asc",
            limit_page_length=MAX_CATEGORIES + 1,
        )
        if len(rows or []) > MAX_CATEGORIES:
            raise CatalogDataError("Catalog category limit exceeded.")
        return [dict(row) for row in (rows or [])]

    def load_category_attribute_rows(self, category_names: list[str]) -> list[dict[str, Any]]:
        if not category_names:
            return []
        limit = min(MAX_CATEGORIES * MAX_CATEGORY_ATTRIBUTES, 100_000)
        rows = frappe.get_all(
            "AOS Category Attribute Row",
            filters={
                "parenttype": "AOS Category",
                "parentfield": "attributes",
                "parent": ["in", category_names],
            },
            fields=[
                "name",
                "parent",
                "idx",
                "attribute",
                "sort_order",
                "options_override",
                "is_required",
                "is_active",
            ],
            order_by="parent asc, idx asc, name asc",
            limit_page_length=limit + 1,
        )
        if len(rows or []) > limit:
            raise CatalogDataError("Catalog attribute row limit exceeded.")
        return [dict(row) for row in (rows or [])]

    def load_attributes(self, attribute_names: list[str]) -> dict[str, dict[str, Any]]:
        names = sorted({str(name) for name in attribute_names if name})
        if not names:
            return {}
        if len(names) > MAX_CATEGORIES * MAX_CATEGORY_ATTRIBUTES:
            raise CatalogDataError("Catalog attribute limit exceeded.")
        rows = frappe.get_all(
            "AOS Ad Attribute",
            filters={"name": ["in", names]},
            fields=["name", "label", "field_type", "unit", "help_text", "options", "is_active"],
            limit_page_length=len(names),
        )
        return {str(row["name"]): dict(row) for row in (rows or [])}
