"""Derived-discovery invalidation for finalized dependency-domain signals.

The owning domains remain authoritative. These hooks only schedule bounded Ads
reprojection when Seller/account/verification signals used by discovery change.
"""
from __future__ import annotations

from typing import Any

import frappe

_BATCH_SIZE = 200
_QUEUE = "long"
_TIMEOUT_SECONDS = 900


def _changed(doc: Any, fields: tuple[str, ...]) -> bool:
    try:
        return any(bool(doc.has_value_changed(field)) for field in fields)
    except Exception:
        return True


def _enqueue_seller_refresh(seller_name: str, *, source: str) -> None:
    clean = str(seller_name or "").strip()
    if not clean:
        return
    frappe.enqueue(
        "aos.services.marketplace_discovery.signals.refresh_seller_ads",
        queue=_QUEUE,
        timeout=_TIMEOUT_SECONDS,
        enqueue_after_commit=True,
        seller_name=clean,
        source=source,
        job_id=f"aos:marketplace-discovery:seller-refresh:{clean}",
        deduplicate=True,
    )


def seller_signal_changed(doc: Any, method: str | None = None) -> None:
    del method
    if _changed(doc, ("status", "shop_name", "user")):
        _enqueue_seller_refresh(getattr(doc, "name", ""), source="seller_signal_changed")


def profile_signal_changed(doc: Any, method: str | None = None) -> None:
    del method
    if not _changed(doc, ("account_status", "is_verified")):
        return
    user = str(getattr(doc, "user", "") or "").strip()
    seller = frappe.db.get_value("AOS Seller", {"user": user}, "name") if user else None
    if seller:
        _enqueue_seller_refresh(str(seller), source="profile_signal_changed")


def user_signal_changed(doc: Any, method: str | None = None) -> None:
    del method
    if not _changed(doc, ("enabled",)):
        return
    user = str(getattr(doc, "name", "") or "").strip()
    seller = frappe.db.get_value("AOS Seller", {"user": user}, "name") if user else None
    if seller:
        _enqueue_seller_refresh(str(seller), source="user_signal_changed")


def refresh_seller_ads(*, seller_name: str, source: str, after: str = "", batch_size: int = _BATCH_SIZE) -> dict[str, Any]:
    """Reproject one Seller's Ads in bounded keyset batches.

    Canonical public reads already fail closed against current Seller/account
    state. This task keeps disposable ranking/vector indexes fresh without
    adding work to the dependency domain's transaction.
    """
    clean_seller = str(seller_name or "").strip()
    clean_after = str(after or "").strip()
    size = max(1, min(int(batch_size or _BATCH_SIZE), 500))
    if not clean_seller:
        return {"ok": True, "processed": 0, "has_more": False}

    filters: dict[str, Any] = {"seller": clean_seller}
    if clean_after:
        filters["name"] = [">", clean_after]
    rows = frappe.get_all(
        "AOS Ad",
        filters=filters,
        fields=["name", "status"],
        order_by="name asc",
        limit=size + 1,
    )
    page = rows[:size]
    from aos.services.ads.indexing import enqueue_discovery_refresh

    for row in page:
        enqueue_discovery_refresh(row.name, status=row.status, source=source)

    has_more = len(rows) > size
    if has_more and page:
        next_after = str(page[-1].name)
        frappe.enqueue(
            "aos.services.marketplace_discovery.signals.refresh_seller_ads",
            queue=_QUEUE,
            timeout=_TIMEOUT_SECONDS,
            enqueue_after_commit=True,
            seller_name=clean_seller,
            source=source,
            after=next_after,
            batch_size=size,
            job_id=f"aos:marketplace-discovery:seller-refresh:{clean_seller}:{next_after}",
            deduplicate=True,
        )
    return {"ok": True, "processed": len(page), "has_more": has_more}
