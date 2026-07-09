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

from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok

from .constants import GET_CATEGORIES_LIMIT_PER_HOUR_PER_IP


def _serialize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row.get("name"),
        "name": row.get("category_name") or row.get("name"),
        "icon": row.get("icon") or "",
        "icon_media": row.get("icon_media") or None,
        "icon_media_id": row.get("icon_media") or None,
        "parent_id": row.get("parent_aos_category") or None,
        "sort_order": int(row.get("sort_order") or 0),
        "is_group": int(row.get("is_group") or 0),
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


def get_categories_impl(**_):
    """Return active categories as a nested tree."""

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
        rows = frappe.get_all(
            "AOS Category",
            filters={"is_active": 1},
            fields=[
                "name",
                "category_name",
                "icon",
                "icon_media",
                "parent_aos_category",
                "sort_order",
                "is_group",
            ],
            order_by="sort_order asc, category_name asc",
            limit_page_length=1000,
        )

        items = [_serialize_row(r) for r in (rows or [])]

        tree = _build_tree(items)

        return ok(
            "Categories fetched.",
            data=tree,
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Get Categories Failed",
        )

        return fail(
            "Failed to fetch categories.",
            error="INTERNAL_ERROR",
        )
