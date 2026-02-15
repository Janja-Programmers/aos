from __future__ import annotations
from typing import Set

import frappe


def get_active_wishlist_ad_ids(user: str) -> Set[str]:
    if not user or user == "Guest":
        return set()

    rows = frappe.get_all(
        "AOS Wishlist",
        filters={
            "user": user,
            "status": "Active",
        },
        pluck="ad",
    )
    return set(rows or [])
