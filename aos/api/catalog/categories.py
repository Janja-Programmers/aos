"""Categories implementation.

DocType: AOS Category

This endpoint is used by:
- Home (categories row)
- Categories tab (tree view)
- Create Listing (category picker)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe

from aos.api.auth.rate_limit import rate_limit, request_ip
from aos.api.auth.responses import fail, ok

from .constants import GET_CATEGORIES_LIMIT_PER_HOUR_PER_IP


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("name"),
        "name": row.get("category_name") or row.get("name"),
        "icon": row.get("icon") or "",
        "parent_id": row.get("parent_aos_category") or None,
        "sort_order": int(row.get("sort_order") or 0),
        "is_group": int(row.get("is_group") or 0),
        "is_active": int(row.get("is_active") or 0),
        "children": [],
    }


def _build_tree(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id: Dict[str, Dict[str, Any]] = {it["id"]: it for it in items}
    roots: List[Dict[str, Any]] = []

    for it in items:
        parent_id: Optional[str] = it.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(it)
        else:
            # parent not present (e.g. inactive filtered out) → treat as root
            roots.append(it)

    def sort_key(x: Dict[str, Any]):
        return (int(x.get("sort_order") or 0), (x.get("name") or "").lower())

    def sort_rec(node: Dict[str, Any]):
        node["children"].sort(key=sort_key)
        for c in node["children"]:
            sort_rec(c)

    roots.sort(key=sort_key)
    for r in roots:
        sort_rec(r)

    return roots


def get_categories_impl(include_inactive: bool = False):
    """Return categories as a nested tree.

    - Default behavior: only active categories.
    - Optional include_inactive=True: include inactive (useful for admin/testing).
    """

    # rate limit by IP (public endpoint)
    rl = rate_limit(
        key=f"aos:catalog:cats:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GET_CATEGORIES_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if rl:
        return rl

    try:
        filters = {}
        if not include_inactive:
            filters["is_active"] = 1

        rows = frappe.get_all(
            "AOS Category",
            filters=filters,
            fields=[
                "name",
                "category_name",
                "icon",
                "parent_aos_category",
                "sort_order",
                "is_group",
                "is_active",
            ],
            order_by="sort_order asc, category_name asc",
            limit_page_length=1000,
        )

        items = [_serialize_row(r) for r in (rows or [])]
        tree = _build_tree(items)
        return ok("Categories fetched.", data=tree)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Get Categories Failed")
        return fail("Failed to fetch categories.", code="INTERNAL_ERROR")
