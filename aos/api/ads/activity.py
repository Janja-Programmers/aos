"""Activity Center hooks for Ads.

This module keeps Activity Center payload creation out of the core Ads,
Wishlist, and Reports endpoints. Ads-related doctypes remain the source of
truth; AOS User Activity is the private user-facing history layer.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.activity.producer import best_effort_activity
from aos.services.activity_service import ActivityService
from aos.services.sellers.identity import public_seller_id_for_name

AD_DOCTYPE = "AOS Ad"
AD_REPORT_DOCTYPE = "AOS Ad Report"
AD_ACTIVITY_GROUP = "Ads"

AD_VIEW_ACTIVITY = "ad_view"
AD_WISHLIST_ACTIVITY = "ad_wishlist"
AD_POSTED_ACTIVITY = "ad_posted"
AD_REPORT_ACTIVITY = "ad_report"

ROUTE_TYPE_AD = "ad"


def ad_view_unique_key(ad_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=AD_VIEW_ACTIVITY,
        target_doctype=AD_DOCTYPE,
        target_name=ad_id,
        route_type=ROUTE_TYPE_AD,
        route_id=ad_id,
    )


def ad_wishlist_unique_key(ad_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=AD_WISHLIST_ACTIVITY,
        target_doctype=AD_DOCTYPE,
        target_name=ad_id,
        route_type=ROUTE_TYPE_AD,
        route_id=ad_id,
    )


def ad_posted_unique_key(ad_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=AD_POSTED_ACTIVITY,
        target_doctype=AD_DOCTYPE,
        target_name=ad_id,
        route_type=ROUTE_TYPE_AD,
        route_id=ad_id,
    )


def ad_report_unique_key(report_id: str) -> str:
    return ActivityService.build_unique_key(
        activity_type=AD_REPORT_ACTIVITY,
        target_doctype=AD_REPORT_DOCTYPE,
        target_name=report_id,
        route_type=ROUTE_TYPE_AD,
        route_id=report_id,
    )


def _compact_text(value: str | None, *, max_len: int = 120) -> str:
    value = " ".join((value or "").strip().split())
    if not value:
        return ""

    if len(value) <= max_len:
        return value

    return value[: max_len - 1].rstrip() + "…"


def _get_primary_ad_image(ad_id: str) -> str:
    rows = frappe.get_all(
        "AOS Ad Image",
        filters={
            "parent": ad_id,
            "parenttype": AD_DOCTYPE,
        },
        fields=["media", "is_primary", "sort_order"],
        order_by="is_primary desc, sort_order asc",
        limit=1,
    )

    if not rows:
        return ""

    try:
        from aos.api.ads.media import get_ad_image_url, project_ad_image_urls

        project_ad_image_urls(rows)
        return get_ad_image_url(rows[0])
    except Exception:
        return ""


def _load_ad_target(ad_id: str | None) -> dict[str, Any] | None:
    ad_id = (ad_id or "").strip()
    if not ad_id:
        return None

    ad = frappe.db.get_value(
        AD_DOCTYPE,
        ad_id,
        [
            "name",
            "public_id",
            "title",
            "seller",
            "category",
            "location",
            "country",
            "status",
            "price_type",
            "currency",
            "price",
        ],
        as_dict=True,
    )

    if not ad:
        return None

    seller_user = None
    if ad.seller:
        seller_user = frappe.db.get_value("AOS Seller", ad.seller, "user")

    title = _compact_text(ad.title, max_len=120) or "Ad"

    subtitle_parts = [
        _compact_text(ad.category, max_len=40),
        _compact_text(ad.location, max_len=40),
    ]
    subtitle = " • ".join([part for part in subtitle_parts if part]) or "Ad"

    return {
        "target_doctype": AD_DOCTYPE,
        "target_name": ad.name,
        "target_title": title,
        "target_subtitle": subtitle,
        "target_image": _get_primary_ad_image(ad.name),
        "route_type": ROUTE_TYPE_AD,
        "route_id": ad.public_id,
        "_seller_user": seller_user,
        "metadata": {
            "seller": public_seller_id_for_name(ad.seller),
            "category": ad.category,
            "location": ad.location,
            "country": ad.country,
            "ad_status": ad.status,
            "price_type": ad.price_type,
            "currency": ad.currency,
            "price": ad.price,
        },
    }


def _safe_record(action_name: str, fn, *args, **kwargs) -> str | bool | None:
    return best_effort_activity(action_name, fn, *args, **kwargs)


def record_ad_view_activity(
    *,
    user: str | None,
    ad_id: str,
) -> str | None:
    """Record/de-dupe a user's ad view history item.

    Own-ad views are ignored because posted ads are tracked separately through
    ad_posted activity.
    """
    if not user:
        return None

    target = _safe_record("load_ad_target", _load_ad_target, ad_id)
    if not target:
        return None

    seller_user = target.pop("_seller_user", None)
    metadata = target.pop("metadata", None) or {}
    if seller_user == user:
        return None

    return _safe_record(
        "record_ad_view_activity",
        ActivityService.record_or_update_activity,
        user=user,
        activity_group=AD_ACTIVITY_GROUP,
        activity_type=AD_VIEW_ACTIVITY,
        metadata=metadata,
        unique_key=ad_view_unique_key(ad_id),
        **target,
    )


def record_ad_wishlist_activity(
    *,
    user: str | None,
    ad_id: str,
) -> str | None:
    """Record/de-dupe a user's ad wishlist history item."""
    if not user:
        return None

    target = _safe_record("load_ad_target", _load_ad_target, ad_id)
    if not target:
        return None

    target.pop("_seller_user", None)
    metadata = target.pop("metadata", None)

    return _safe_record(
        "record_ad_wishlist_activity",
        ActivityService.record_or_update_activity,
        user=user,
        activity_group=AD_ACTIVITY_GROUP,
        activity_type=AD_WISHLIST_ACTIVITY,
        metadata=metadata,
        unique_key=ad_wishlist_unique_key(ad_id),
        **target,
    )


def hide_ad_wishlist_activity(
    *,
    user: str | None,
    ad_id: str,
) -> bool:
    """Hide a wishlist history item after removing an ad from wishlist."""
    if not user:
        return False

    result = _safe_record(
        "hide_ad_wishlist_activity",
        ActivityService.hide_activity_by_unique_key,
        user=user,
        unique_key=ad_wishlist_unique_key(ad_id),
    )

    return bool(result)


def record_ad_posted_activity(
    *,
    user: str | None,
    ad_id: str,
) -> str | None:
    """Record/de-dupe a user's posted ad history item."""
    if not user:
        return None

    target = _safe_record("load_ad_target", _load_ad_target, ad_id)
    if not target:
        return None

    target.pop("_seller_user", None)
    metadata = target.pop("metadata", None)

    return _safe_record(
        "record_ad_posted_activity",
        ActivityService.record_activity,
        user=user,
        activity_group=AD_ACTIVITY_GROUP,
        activity_type=AD_POSTED_ACTIVITY,
        metadata=metadata,
        unique_key=ad_posted_unique_key(ad_id),
        **target,
    )


def record_ad_report_activity(
    *,
    user: str | None,
    ad_id: str,
    report_id: str,
    reason: str | None = None,
) -> str | None:
    """Record one ad report history item."""
    if not user or not report_id:
        return None

    target = _safe_record("load_ad_target", _load_ad_target, ad_id)
    if not target:
        return None

    target.pop("_seller_user", None)
    target.pop("metadata", None)
    metadata = {"reason": _compact_text(reason, max_len=200)}

    target["target_subtitle"] = _compact_text(reason, max_len=120) or "Reported an ad"

    return _safe_record(
        "record_ad_report_activity",
        ActivityService.record_activity,
        user=user,
        activity_group=AD_ACTIVITY_GROUP,
        activity_type=AD_REPORT_ACTIVITY,
        metadata=metadata,
        unique_key=ad_report_unique_key(report_id),
        **target,
    )
