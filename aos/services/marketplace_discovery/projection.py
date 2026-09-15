"""Bounded public Ads projection for derived discovery candidate systems.

Search Ranking and Qdrant return only candidate public ids. This module always
re-checks canonical database eligibility, projects prices through one coherent
FX snapshot, batches Media URL resolution, and applies the final geography
rerank without trusting a derived index for visibility.
"""
from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, getdate, now_datetime, nowdate

from aos.services.ads.constants import MAX_IMAGES
from aos.api.ads.serializers import serialize_ad_list_item
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.services.currency_conversion import sql_conversion_expressions
from aos.services.marketplace_discovery.geography import stable_geographic_rerank
from aos.services.media.media_service import MediaService
from aos.utils.aos_settings import get_aos_settings_snapshot


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _attach_image_urls(images_by_ad: dict[str, list[dict[str, Any]]]) -> None:
    attachments: list[tuple[str, str]] = []
    for ad_name, rows in images_by_ad.items():
        attachments.extend((_clean(row.get("media")), ad_name) for row in rows if _clean(row.get("media")))
    if not attachments:
        return
    urls = MediaService().get_public_attachment_url_map(
        attachments,
        purpose="ad_image",
        attached_doctype="AOS Ad",
        attached_field="images",
    )
    for ad_name, rows in images_by_ad.items():
        for row in rows:
            row["url"] = urls.get((_clean(row.get("media")), ad_name), "")


def load_public_ad_items(
    public_ids: list[str],
    *,
    country: str,
    currency: str,
    location: str = "",
    viewer: str = "Guest",
    limit: int = 20,
    exclude_public_id: str | None = None,
) -> list[dict[str, Any]]:
    """Hydrate bounded derived-index candidates through canonical DB truth.

    Candidate order is treated as the primary ranking order. Geography is
    intentionally applied last and remains stable within each locality bucket.
    """
    ordered_ids: list[str] = []
    seen: set[str] = set()
    for value in public_ids:
        clean = _clean(value)
        if not clean or clean in seen or clean == _clean(exclude_public_id):
            continue
        seen.add(clean)
        ordered_ids.append(clean)
        if len(ordered_ids) >= 500:
            break
    if not ordered_ids:
        return []

    settings = get_aos_settings_snapshot()
    today = getdate(nowdate())
    values: dict[str, Any] = {
        "candidate_ids": tuple(ordered_ids),
        "display_currency": currency,
        "base_currency": settings.base_currency,
        "fx_fresh_after": add_to_date(now_datetime(), hours=-settings.fx_max_stale_hours, as_string=True),
        "today": today,
    }
    conditions = [
        "a.public_id IN %(candidate_ids)s",
        "a.status='Active'",
        "s.status='Active'",
        "u.enabled = 1",
        "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
        "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
    ]
    if viewer and viewer != "Guest":
        values["viewer"] = viewer
        conditions.append(
            """NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b
                WHERE b.status='Active'
                  AND ((b.blocker_user=%(viewer)s AND b.blocked_user=s.user)
                    OR (b.blocker_user=s.user AND b.blocked_user=%(viewer)s))
            )"""
        )

    offer_active = """a.offer_price IS NOT NULL AND a.offer_price > 0
        AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
        AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)"""
    native_current = f"CASE WHEN {offer_active} THEN a.offer_price ELSE a.price END"
    original_fx = sql_conversion_expressions(amount_sql="a.price", fresh_after_param="%(fx_fresh_after)s")
    current_fx = sql_conversion_expressions(amount_sql=native_current, fresh_after_param="%(fx_fresh_after)s")

    rows = frappe.db.sql(
        f"""
        SELECT a.name, a.public_id, a.title, a.status, a.country, a.location, loc.location AS location_name, a.category, a.seller,
               a.currency, {original_fx['currency']} AS display_currency,
               %(display_currency)s AS requested_display_currency,
               {original_fx['available']} AS conversion_available,
               {original_fx['rate']} AS conversion_rate,
               a.price_type, a.price, a.offer_price, a.offer_start_date, a.offer_end_date,
               a.offer_percent, a.price_unit, a.average_rating, a.total_reviews, a.creation,
               {original_fx['amount']} AS original_price_converted,
               {current_fx['amount']} AS current_price
        FROM `tabAOS Ad` a
        INNER JOIN `tabAOS Seller` s ON s.name=a.seller
        LEFT JOIN `tabAOS Location` loc ON loc.name=a.location
        INNER JOIN `tabAOS Profile` p ON p.user=s.user
        INNER JOIN `tabUser` u ON u.name=s.user
        LEFT JOIN `tabAOS Exchange Rate` er_source ON er_source.currency=a.currency
        LEFT JOIN `tabAOS Exchange Rate` er_target ON er_target.currency=%(display_currency)s
        WHERE {' AND '.join(conditions)}
        """,
        values,
        as_dict=True,
    )
    by_public = {_clean(row.public_id): row for row in rows}
    primary_rows = [by_public[public_id] for public_id in ordered_ids if public_id in by_public]
    if not primary_rows:
        return []

    names = [row.name for row in primary_rows]
    images_by_ad: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    image_rows = frappe.get_all(
        "AOS Ad Image",
        filters={"parenttype": "AOS Ad", "parent": ["in", names]},
        fields=["parent", "media", "is_primary", "sort_order"],
        order_by="parent asc, is_primary desc, sort_order asc, name asc",
        limit=max(1, len(names) * MAX_IMAGES),
    )
    for image in image_rows:
        images_by_ad.setdefault(image.parent, []).append(dict(image))
    _attach_image_urls(images_by_ad)

    primary_rows = stable_geographic_rerank(
        primary_rows,
        country=country,
        location=location,
        country_key="country",
        location_key="location",
    )
    wishlisted = get_active_wishlist_ad_ids(viewer) if viewer != "Guest" else set()
    result: list[dict[str, Any]] = []
    for row in primary_rows[: max(1, min(int(limit or 20), 100))]:
        row.images = images_by_ad.get(row.name, [])
        row.is_offer_active = bool(
            row.offer_price
            and (row.offer_start_date is None or row.offer_start_date <= today)
            and (row.offer_end_date is None or row.offer_end_date >= today)
        )
        result.append(serialize_ad_list_item(row, is_wishlisted=row.name in wishlisted))
    return result
