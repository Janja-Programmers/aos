"""Bounded persistence queries for the Catalog domain."""

from __future__ import annotations

from typing import Any

import frappe

from .constants import MAX_CATEGORIES, MAX_CATEGORY_ATTRIBUTES, MAX_CATEGORY_ATTRIBUTE_DEPENDENCIES
from .errors import CatalogDataError

_CATEGORY_FIELDS = [
    "name",
    "category_name",
    "image_media",
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

    def load_category(self, category_id: str) -> dict[str, Any] | None:
        rows = frappe.get_all(
            "AOS Category",
            filters={"name": str(category_id or "").strip()},
            fields=_CATEGORY_FIELDS,
            limit=1,
        )
        if not rows:
            return None
        return dict(rows[0])

    def load_categories(self) -> list[dict[str, Any]]:
        rows = frappe.get_all(
            "AOS Category",
            fields=_CATEGORY_FIELDS,
            order_by="sort_order asc, category_name asc, name asc",
            limit=MAX_CATEGORIES + 1,
        )
        if len(rows or []) > MAX_CATEGORIES:
            raise CatalogDataError("Catalog category limit exceeded.")
        return [dict(row) for row in (rows or [])]

    def load_category_attribute_rows(self, category_names: list[str]) -> list[dict[str, Any]]:
        names = sorted({str(name).strip() for name in category_names if str(name).strip()})
        if not names:
            return []
        if len(names) > MAX_CATEGORY_DEPTH_QUERY_COUNT:
            raise CatalogDataError("Catalog category chain is invalid.")

        limit = len(names) * MAX_CATEGORY_ATTRIBUTES
        rows = frappe.get_all(
            "AOS Category Attribute Row",
            filters={
                "parenttype": "AOS Category",
                "parentfield": "attributes",
                "parent": ["in", names],
            },
            fields=[
                "name",
                "parent",
                "idx",
                "attribute",
                "sort_order",
                "options_override",
                "depends_on_attribute",
                "is_required",
                "is_active",
            ],
            order_by="parent asc, sort_order asc, idx asc, name asc",
            limit=limit + 1,
        )
        if len(rows or []) > limit:
            raise CatalogDataError("Catalog attribute row limit exceeded.")
        return [dict(row) for row in (rows or [])]


    def load_category_attribute_dependency_rows(
        self, category_names: list[str]
    ) -> list[dict[str, Any]]:
        names = sorted({str(name).strip() for name in category_names if str(name).strip()})
        if not names:
            return []
        if len(names) > MAX_CATEGORY_DEPTH_QUERY_COUNT:
            raise CatalogDataError("Catalog category chain is invalid.")

        limit = len(names) * MAX_CATEGORY_ATTRIBUTE_DEPENDENCIES
        rows = frappe.get_all(
            "AOS Category Attribute Dependency Row",
            filters={
                "parenttype": "AOS Category",
                "parentfield": "attribute_dependencies",
                "parent": ["in", names],
            },
            fields=[
                "name",
                "parent",
                "idx",
                "child_attribute",
                "parent_option",
                "child_options",
            ],
            order_by="parent asc, child_attribute asc, idx asc, name asc",
            limit=limit + 1,
        )
        if len(rows or []) > limit:
            raise CatalogDataError("Catalog attribute dependency row limit exceeded.")
        return [dict(row) for row in (rows or [])]

    def load_attributes(self, attribute_names: list[str]) -> dict[str, dict[str, Any]]:
        names = sorted({str(name).strip() for name in attribute_names if str(name).strip()})
        if not names:
            return {}
        if len(names) > MAX_ATTRIBUTE_DEFINITIONS_PER_SCHEMA:
            raise CatalogDataError("Catalog attribute limit exceeded.")
        rows = frappe.get_all(
            "AOS Ad Attribute",
            filters={"name": ["in", names]},
            fields=[
                "name",
                "attribute_key",
                "label",
                "field_type",
                "unit",
                "help_text",
                "options",
                "is_active",
            ],
            limit=len(names) + 1,
        )
        if len(rows or []) > len(names):
            raise CatalogDataError("Catalog attribute query returned duplicate definitions.")
        return {str(row["name"]): dict(row) for row in (rows or [])}


# A resolved public schema can only contain the configured two-level chain.
MAX_CATEGORY_DEPTH_QUERY_COUNT = 2
MAX_ATTRIBUTE_DEFINITIONS_PER_SCHEMA = MAX_CATEGORY_DEPTH_QUERY_COUNT * MAX_CATEGORY_ATTRIBUTES
