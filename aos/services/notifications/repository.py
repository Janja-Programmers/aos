"""Small query helpers shared by Notification API and realtime delivery."""

from __future__ import annotations

import frappe


def get_unread_count(user: str) -> int:
    user = str(user or "").strip()
    if not user:
        return 0
    return max(
        0,
        int(
            frappe.db.count(
                "AOS Notification",
                filters={"user": user, "is_read": 0},
            )
            or 0
        ),
    )
