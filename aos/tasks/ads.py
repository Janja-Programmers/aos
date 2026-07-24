"""Bounded, idempotent Ads lifecycle scheduler tasks."""

from __future__ import annotations

import frappe
from frappe.utils import getdate, today

from aos.services.ads.indexing import enqueue_discovery_refresh
from aos.services.ads.lifecycle import validate_status_transition
from aos.services.ads.mutations import apply_transition, lock_ad
from aos.services.ads.observability import ads_log
from aos.services.notification_service import NotificationService

_EXPIRY_BATCH_SIZE = 200


def expire_ads() -> None:
    """Expire one bounded batch of overdue public ads.

    Each row is rechecked under a database lock, making retries and overlapping
    scheduler invocations idempotent. The scheduler/worker owns transaction
    durability; this helper intentionally performs no hidden commit.
    """

    due = frappe.get_all(
        "AOS Ad",
        filters={"status": "Active", "expires_on": ["<", today()]},
        fields=["name"],
        order_by="expires_on asc, name asc",
        limit=_EXPIRY_BATCH_SIZE,
    )
    expired = 0
    for row in due:
        try:
            lock_ad(row.name)
            ad = frappe.get_doc("AOS Ad", row.name)
            if ad.status != "Active" or not ad.expires_on or getdate(ad.expires_on) >= getdate(today()):
                continue
            transition = validate_status_transition(ad.status, "Expired", action="expire")
            apply_transition(ad, transition)
            ad.save(ignore_permissions=True)
            seller_user = frappe.db.get_value("AOS Seller", ad.seller, "user")
            if seller_user:
                NotificationService.notify_ad_expired(user=seller_user, ad_id=ad.name, title=ad.title)
            enqueue_discovery_refresh(ad.name, status=ad.status, source="ad_expiry")
            expired += 1
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS expire ad row failed")
    ads_log("expiry_batch", status="Expired", outcome="success", count=expired)
