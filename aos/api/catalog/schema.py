"""Category schema resolver (attributes + pricing).

This endpoint provides the dynamic form schema for a selected category.

Attributes:
  - attributes are linked to categories via the AOS Category Attribute Row child table
  - child categories inherit parent attributes
  - child categories may override select options via `options_override`

Pricing:
  - pricing requirement (Required/Optional/Hidden)
  - allowed price types (Fixed/Negotiable/Contact for price/Free)
  - allowed price units (services only)
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import GET_CATEGORY_SCHEMA_LIMIT_PER_HOUR_PER_IP


def _split_lines(val: Any) -> List[str]:
    """Split newline-separated strings into trimmed, non-empty values."""

    if not val:
        return []

    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]

    return [ln.strip() for ln in str(val).splitlines() if ln.strip()]


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _attribute_key(attribute_name: str) -> str:
    """Stable key derived from attribute DocType name."""

    try:
        return frappe.scrub(attribute_name or "")
    except Exception:
        return (attribute_name or "").strip().lower().replace(" ", "_")


def _get_category_chain(category: str) -> List[Dict[str, Any]]:
    """Return category chain from leaf -> root."""

    chain: List[Dict[str, Any]] = []
    seen: set[str] = set()
    current = category

    while current and current not in seen:
        seen.add(current)

        doc = frappe.get_doc("AOS Category", current)

        chain.append(
            {
                "name": doc.name,
                "category_name": getattr(doc, "category_name", None) or doc.name,
                "parent": getattr(doc, "parent_aos_category", None),
                "sort_order": _safe_int(getattr(doc, "sort_order", 0)),
                "is_service": _safe_int(getattr(doc, "is_service", 0)),
                "pricing_requirement": (
                    getattr(doc, "pricing_requirement", None) or ""
                ).strip(),
                "allowed_price_types": getattr(doc, "allowed_price_types", None) or "",
                "allowed_price_units": getattr(doc, "allowed_price_units", None) or "",
                "attributes": list(getattr(doc, "attributes", []) or []),
            }
        )

        current = getattr(doc, "parent_aos_category", None)

    return chain


def _resolve_pricing(chain_leaf_to_root: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve pricing rules using the leaf category."""

    leaf = chain_leaf_to_root[0]

    requirement = (leaf.get("pricing_requirement") or "Optional").strip() or "Optional"
    allowed_types = _split_lines(leaf.get("allowed_price_types"))
    allowed_units = _split_lines(leaf.get("allowed_price_units"))
    is_service = int(leaf.get("is_service") or 0)

    pricing: Dict[str, Any] = {
        "pricing_requirement": requirement,
    }

    if allowed_types:
        pricing["allowed_price_types"] = allowed_types

    # only services support price units
    if is_service and allowed_units:
        pricing["allowed_price_units"] = allowed_units

    return pricing


def _resolve_attributes(chain_leaf_to_root: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Resolve attributes with inheritance.

    Parent attributes are inherited.
    Child category rows override parent rows.
    Child can explicitly disable inherited attributes.
    """

    by_attr: Dict[str, Dict[str, Any]] = {}
    disabled: set[str] = set()

    # iterate root -> leaf so leaf overrides
    for cat in reversed(chain_leaf_to_root):
        for row in (cat.get("attributes") or []):
            attr_name = getattr(row, "attribute", None) or ""
            if not attr_name:
                continue

            if not int(getattr(row, "is_active", 0) or 0):
                disabled.add(attr_name)

                # if already inherited, remove it
                if attr_name in by_attr:
                    del by_attr[attr_name]
                continue

            # if previously disabled, skip completely
            if attr_name in disabled:
                continue

            try:
                attr_doc = frappe.get_doc("AOS Ad Attribute", attr_name)
            except Exception:
                continue

            if not int(getattr(attr_doc, "is_active", 0) or 0):
                continue

            required = int(getattr(row, "is_required", 0) or 0)
            sort_order = _safe_int(getattr(row, "sort_order", 0))
            options_override = getattr(row, "options_override", None) or ""

            field_type = (getattr(attr_doc, "field_type", None) or "Text").strip()
            base_options = getattr(attr_doc, "options", None) or ""

            options = _split_lines(options_override) or _split_lines(base_options)

            by_attr[attr_name] = {
                "id": attr_doc.name,
                "key": _attribute_key(attr_doc.name),
                "label": getattr(attr_doc, "label", None) or attr_doc.name,
                "type": field_type,
                "required": required,
                "unit": getattr(attr_doc, "unit", None) or "",
                "help_text": getattr(attr_doc, "help_text", None) or "",
                "options": options,
                "sort_order": sort_order,
            }

    return sorted(
        by_attr.values(),
        key=lambda x: (
            _safe_int(x.get("sort_order"), 0),
            (x.get("label") or "").lower(),
        ),
    )


def get_category_schema_impl(**kwargs):
    """Return resolved Attributes + Pricing schema for a category."""

    rl = rate_limit(
        key=f"aos:attributes:schema:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GET_CATEGORY_SCHEMA_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )

    if rl:
        return rl

    category = kwargs.get("category")

    if not category:
        return fail("Category is required.", error="VALIDATION_ERROR")

    try:
        chain = _get_category_chain(category)

        if not chain:
            return fail("Category not found.", error="NOT_FOUND")

        leaf = chain[0]

        attributes = _resolve_attributes(chain)
        pricing = _resolve_pricing(chain)

        data = {
            "category": {
                "id": leaf["name"],
                "name": leaf["category_name"],
                "parent_id": leaf["parent"],
                "is_service": int(leaf.get("is_service") or 0),
            },
            "attributes": attributes,
            "pricing": pricing,
        }

        return ok("Category schema fetched.", data=data)

    except frappe.DoesNotExistError:
        return fail("Category not found.", error="NOT_FOUND")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Category Schema Failed",
        )

        return fail(
            "Failed to fetch category schema.",
            error="INTERNAL_ERROR",
        )
