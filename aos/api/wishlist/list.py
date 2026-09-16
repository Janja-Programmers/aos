"""Bounded, privacy-safe wishlist listing."""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import add_days, getdate, nowdate

from aos.api.ads.category_filters import resolve_category_filter_values
from aos.api.ads.media import project_ad_image_urls
from aos.api.ads.serializers import serialize_ad_list_item
from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.api.shared.sql_safety import safe_like_contains
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import MAX_IMAGES
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import (
    decode_wishlist_cursor,
    encode_wishlist_cursor,
    normalize_wishlist_list_filters,
)
from aos.services.currency_conversion import sql_conversion_expressions
from aos.utils.aos_settings import get_aos_settings_snapshot

from .constants import WISHLIST_LIST_LIMIT_PER_MINUTE_PER_USER


def _pagination(
    *,
    limit: int,
    offset: int,
    returned: int,
    has_more: bool,
    next_cursor: str | None,
    cursor_mode: bool,
) -> dict[str, Any]:
    return {
        "limit": limit,
        "offset": offset,
        "returned": returned,
        "has_more": bool(has_more),
        "next_offset": None if cursor_mode or not has_more else offset + returned,
        "next_cursor": next_cursor,
    }


def _empty(*, limit: int, offset: int, cursor_mode: bool = False):
    return ok(
        "Wishlist fetched.",
        data={
            "items": [],
            "pagination": _pagination(
                limit=limit,
                offset=offset,
                returned=0,
                has_more=False,
                next_cursor=None,
                cursor_mode=cursor_mode,
            ),
        },
    )


def _build_order_by(
    *,
    sort: str,
    cursor_mode: bool,
    saved_on_sql: str,
    geo_boost: str,
    verified_boost: str,
    conversion_available_sql: str,
    current_price_sql: str,
) -> str:
    """Build deterministic ordering with the explicit user sort as primary."""

    saved_tie_breakers = f"{saved_on_sql} DESC, w.name DESC, a.name DESC"
    if cursor_mode:
        return saved_tie_breakers

    market_tie_breakers = f"{geo_boost}, {verified_boost}, {saved_tie_breakers}"
    if sort == "recent":
        return f"{saved_on_sql} DESC, w.name DESC, {geo_boost}, {verified_boost}, a.name DESC"
    if sort == "rating_high":
        return f"a.average_rating DESC, a.total_reviews DESC, {market_tie_breakers}"
    if sort == "price_low":
        return (
            f"{conversion_available_sql} DESC, {current_price_sql} ASC, "
            f"{market_tie_breakers}"
        )
    return (
        f"{conversion_available_sql} DESC, {current_price_sql} DESC, "
        f"{market_tie_breakers}"
    )


def list_wishlist_impl(**kwargs):
    user, auth_error = require_login()
    if auth_error:
        return auth_error

    limited = rate_limit(
        key=rate_limit_key("wishlist", "list", user, request_ip()),
        ttl_seconds=60,
        limit=WISHLIST_LIST_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _list():
        filters = normalize_wishlist_list_filters(kwargs)
        if filters["q"] and len(filters["q"]) < 2:
            raise AdsValidationError(
                "Search query must contain at least two characters.",
                code="INVALID_SEARCH_QUERY",
            )
        if filters["cursor"] and (
            filters["sort"] != "recent"
            or filters["offset"] != 0
            or filters["q"]
        ):
            raise AdsValidationError(
                "Wishlist cursor pagination requires recent sort, zero offset, and no search query.",
                code="INVALID_WISHLIST_CURSOR",
            )

        cursor_values = (
            decode_wishlist_cursor(filters["cursor"])
            if filters["cursor"]
            else None
        )

        country, display_currency, market_error = resolve_market_context(
            country=filters["country"],
            currency=filters["currency"],
        )
        if market_error:
            return market_error

        limit = filters["limit"]
        offset = filters["offset"]
        cursor = filters["cursor"]
        cursor_mode = bool(cursor)
        today = getdate(nowdate())
        settings = get_aos_settings_snapshot()
        base_currency = settings.base_currency

        if filters["seller"] and not frappe.db.exists("AOS Seller", filters["seller"]):
            return _empty(limit=limit, offset=offset, cursor_mode=cursor_mode)

        saved_on_sql = "COALESCE(w.saved_on, w.creation)"
        conditions = [
            "w.user = %(user)s",
            "w.status = 'Active'",
            "a.status = 'Active'",
            "a.public_id IS NOT NULL",
            "a.public_id <> ''",
            "seller.status = 'Active'",
            "seller_user.enabled = 1",
            "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
            """NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b
                WHERE b.status = 'Active'
                  AND ((b.blocker_user = %(user)s AND b.blocked_user = seller.user)
                    OR (b.blocker_user = seller.user AND b.blocked_user = %(user)s))
            )""",
        ]
        values: Dict[str, Any] = {
            "user": user,
            "country": country,
            "today": today,
            "display_currency": display_currency,
            "base_currency": base_currency,
            "limit": limit + 1,
            "offset": 0 if cursor_mode else offset,
        }

        if cursor_values:
            cursor_saved_on, cursor_name = cursor_values
            conditions.append(
                f"({saved_on_sql} < %(cursor_saved_on)s OR "
                f"({saved_on_sql} = %(cursor_saved_on)s AND w.name < %(cursor_name)s))"
            )
            values["cursor_saved_on"] = cursor_saved_on
            values["cursor_name"] = cursor_name

        if filters["seller"]:
            conditions.append("a.seller = %(seller)s")
            values["seller"] = filters["seller"]
        if filters["category"]:
            category_ids = resolve_category_filter_values(filters["category"])
            if not category_ids:
                return _empty(limit=limit, offset=offset, cursor_mode=cursor_mode)
            conditions.append("a.category IN %(categories)s")
            values["categories"] = tuple(category_ids)
        if filters["q"]:
            conditions.append("a.title LIKE %(q)s ESCAPE '\\\\'")
            values["q"] = safe_like_contains(filters["q"])
        if filters["price_type"]:
            conditions.append("a.price_type = %(price_type)s")
            values["price_type"] = filters["price_type"]
        if filters["rating_min"] is not None:
            conditions.append("a.average_rating >= %(rating_min)s")
            values["rating_min"] = filters["rating_min"]
        if filters["verified_seller"]:
            conditions.append("COALESCE(p.is_verified, 0) = 1")

        offer_active_sql = """
            a.offer_price IS NOT NULL AND a.offer_price > 0
            AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
            AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)
        """
        native_current_price = f"CASE WHEN {offer_active_sql} THEN a.offer_price ELSE a.price END"
        original_conversion = sql_conversion_expressions(amount_sql="a.price")
        current_conversion = sql_conversion_expressions(amount_sql=native_current_price)
        original_price_sql = original_conversion["amount"]
        current_price_sql = current_conversion["amount"]

        if filters["promotion_type"] in {"offer", "flash_sale", "deal"}:
            conditions.append(f"({offer_active_sql})")
        if filters["promotion_type"] == "flash_sale":
            conditions.append("a.offer_end_date BETWEEN %(today)s AND %(flash_end)s")
            values["flash_end"] = add_days(today, settings.flash_sale_window_days)
        if filters["price_min"] is not None:
            conditions.extend(
                [
                    f"({original_conversion['available']}) = 1",
                    f"{current_price_sql} >= %(price_min)s",
                ]
            )
            values["price_min"] = filters["price_min"]
        if filters["price_max"] is not None:
            conditions.extend(
                [
                    f"({original_conversion['available']}) = 1",
                    f"{current_price_sql} <= %(price_max)s",
                ]
            )
            values["price_max"] = filters["price_max"]

        verified_boost = "COALESCE(p.is_verified, 0) DESC"
        geo_parts = ["CASE WHEN a.country = %(country)s THEN 0 ELSE 1 END"]
        if filters["location"]:
            values["location"] = filters["location"]
            geo_parts.append("CASE WHEN a.location = %(location)s THEN 0 ELSE 1 END")
        geo_boost = ", ".join(geo_parts)
        order_by = _build_order_by(
            sort=filters["sort"],
            cursor_mode=cursor_mode,
            saved_on_sql=saved_on_sql,
            geo_boost=geo_boost,
            verified_boost=verified_boost,
            conversion_available_sql=original_conversion["available"],
            current_price_sql=current_price_sql,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                w.name AS wishlist_id,
                {saved_on_sql} AS wishlist_saved_on,
                a.name, a.public_id, a.title, a.status, a.country, a.location, a.category, a.seller,
                a.currency, {original_conversion['currency']} AS display_currency,
                %(display_currency)s AS requested_display_currency,
                {original_conversion['available']} AS conversion_available,
                {original_conversion['rate']} AS conversion_rate,
                a.price_type, a.price, a.price_unit, a.offer_price,
                a.offer_start_date, a.offer_end_date, a.offer_percent,
                a.average_rating, a.total_reviews, a.creation,
                COALESCE(p.is_verified, 0) AS is_verified,
                {original_price_sql} AS original_price_converted,
                {current_price_sql} AS current_price
            FROM `tabAOS Wishlist` w
            INNER JOIN `tabAOS Ad` a ON a.name = w.ad
            INNER JOIN `tabAOS Seller` seller ON seller.name = a.seller
            INNER JOIN `tabAOS Profile` p ON p.user = seller.user
            INNER JOIN `tabUser` seller_user ON seller_user.name = seller.user
            LEFT JOIN `tabAOS Exchange Rate` er_source ON er_source.currency = a.currency
            LEFT JOIN `tabAOS Exchange Rate` er_target ON er_target.currency = %(display_currency)s
            WHERE {' AND '.join(conditions)}
            ORDER BY {order_by}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            values,
            as_dict=True,
        )

        has_more = len(rows) > limit
        rows = rows[:limit]
        ad_names = [row.name for row in rows]
        images_by_ad: Dict[str, List[Dict[str, Any]]] = {name: [] for name in ad_names}
        if ad_names:
            image_rows = frappe.get_all(
                "AOS Ad Image",
                filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
                fields=["parent", "media", "is_primary", "sort_order"],
                order_by="parent asc, is_primary desc, sort_order asc, name asc",
                limit=max(1, len(ad_names) * MAX_IMAGES),
            )
            project_ad_image_urls(image_rows)
            for image in image_rows:
                images_by_ad.setdefault(image.parent, []).append(image)

        items = []
        for row in rows:
            row.images = images_by_ad.get(row.name, [])
            row.is_offer_active = bool(
                row.offer_price
                and (row.offer_start_date is None or row.offer_start_date <= today)
                and (row.offer_end_date is None or row.offer_end_date >= today)
            )
            item = serialize_ad_list_item(row, is_wishlisted=True)
            item["wishlisted_on"] = str(row.wishlist_saved_on or "") or None
            items.append(item)

        next_cursor = None
        cursor_eligible = filters["sort"] == "recent" and offset == 0 and not filters["q"]
        if has_more and rows and cursor_eligible:
            last = rows[-1]
            next_cursor = encode_wishlist_cursor(
                saved_on=last.wishlist_saved_on,
                name=last.wishlist_id,
            )

        return ok(
            "Wishlist fetched.",
            data={
                "items": items,
                "pagination": _pagination(
                    limit=limit,
                    offset=offset,
                    returned=len(items),
                    has_more=has_more,
                    next_cursor=next_cursor,
                    cursor_mode=cursor_mode,
                ),
            },
        )

    return run_ads_api(
        _list,
        fallback="Failed to fetch wishlist.",
        log_title="AOS Wishlist List Failed",
    )
