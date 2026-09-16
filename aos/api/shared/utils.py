from __future__ import annotations

from collections.abc import Iterable
from typing import Set

import frappe


def get_active_wishlist_ad_ids(user: str, *, ad_ids: Iterable[str]) -> Set[str]:
    """Return active Wishlist state only for a bounded caller-supplied Ad set."""

    if not user or user == "Guest":
        return set()

    bounded = tuple(dict.fromkeys(str(ad_id or "").strip() for ad_id in ad_ids if str(ad_id or "").strip()))
    if not bounded:
        return set()
    if len(bounded) > 500:
        bounded = bounded[:500]

    rows = frappe.get_all(
        "AOS Wishlist",
        filters={
            "user": user,
            "status": "Active",
            "ad": ["in", bounded],
        },
        pluck="ad",
        limit=len(bounded),
    )
    return set(rows or [])
