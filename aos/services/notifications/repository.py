"""Small query helpers shared by Notification API and realtime delivery."""

from __future__ import annotations

import frappe

from aos.services.notifications.contracts import NOTIFICATION_TYPES


def get_unread_count(user: str) -> int:
    user = str(user or "").strip()
    if not user:
        return 0
    return max(
        0,
        int(
            frappe.db.count(
                "AOS Notification",
                filters={
                    "user": user,
                    "is_read": 0,
                    "type": ("in", sorted(NOTIFICATION_TYPES)),
                },
            )
            or 0
        ),
    )
