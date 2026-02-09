"""Category helpers for buyer listing.

Your AOS Category is a 2-level tree:
 - Parents are marked with `is_group = 1`
 - Leaf nodes have `is_group = 0` and point to `parent_aos_category`

For buyer browsing:
 - If a leaf is passed, filter exactly by that leaf.
 - If a parent is passed, include all active leaf children.
"""

from __future__ import annotations

from typing import List

import frappe


def resolve_category_filter_values(category: str) -> List[str]:
    """Return category values to use in an `IN` filter.

    - If `category` is a leaf: returns [category]
    - If `category` is a parent group: returns all active leaf children
    - If missing / inactive / not found: returns []

    Notes:
      * `AOS Ad.category` is a Link to `AOS Category` so values are the category docnames.
      * We keep this helper small and safe (no exceptions leaking to the API).
    """

    category = (category or "").strip()
    if not category:
        return []

    row = frappe.db.get_value(
        "AOS Category",
        category,
        ["is_group", "is_active"],
        as_dict=True,
    )
    if not row or not int(row.is_active or 0):
        return []

    # Leaf: exact match
    if not int(row.is_group or 0):
        return [category]

    # Parent: include all active leaf children (2-level tree)
    cache_key = f"aos:ads:cat_children:{category}"
    try:
        cached = frappe.cache().get_value(cache_key)
        if cached:
            # cache may return tuple/list depending on backend
            return list(cached)
    except Exception:
        cached = None

    children = frappe.get_all(
        "AOS Category",
        filters={
            "parent_aos_category": category,
            "is_active": 1,
            "is_group": 0,
        },
        pluck="name",
        order_by="sort_order asc, category_name asc",
    )

    try:
        # Short TTL cache to reduce DB load during browsing
        frappe.cache().set_value(cache_key, children or [], expires_in_sec=300)
    except Exception:
        pass

    return children or []
