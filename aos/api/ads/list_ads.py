"""Bounded public Ads discovery with privacy-safe filters and stable ordering."""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import add_days, getdate, nowdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.sql_safety import clean_safe_docnames, safe_like_contains
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import MAX_IMAGES
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import decode_recent_cursor, encode_recent_cursor, normalize_public_list_filters
from aos.services.currency_conversion import sql_conversion_expressions
from aos.services.search_ranking_service import search_ad_candidates
from aos.utils.aos_settings import get_aos_settings_snapshot

from .category_filters import resolve_category_filter_values
from .constants import LIST_ADS_LIMIT_PER_MINUTE_PER_IP
from .serializers import serialize_ad_list_item


def _search_order_sql(ad_ids: list[str]) -> str:
    safe_ids = clean_safe_docnames(ad_ids)
    if not safe_ids:
        return ""
    escaped = ", ".join(frappe.db.escape(ad_id) for ad_id in safe_ids)
    return f"FIELD(a.name, {escaped})"


def _build_order_by(
    *,
    candidate_order: str,
    cursor: str,
    sort: str,
    promotion_type: str | None,
    geo_boost: str,
    verified_boost: str,
    conversion_available_sql: str,
    current_price_sql: str,
) -> str:
    """Return deterministic ordering without allowing ranking boosts to mask explicit sorts."""

    if cursor:
        return "a.creation DESC, a.name DESC"

    tie_breakers = f"{geo_boost}, {verified_boost}, a.creation DESC, a.name DESC"
    if sort == "price_low":
        return (
            f"{conversion_available_sql} DESC, {current_price_sql} ASC, "
            f"{tie_breakers}"
        )
    if sort == "price_high":
        return (
            f"{conversion_available_sql} DESC, {current_price_sql} DESC, "
            f"{tie_breakers}"
        )
    if sort == "recent":
        return f"a.creation DESC, {geo_boost}, {verified_boost}, a.name DESC"
    if candidate_order:
        return f"{candidate_order} ASC, {geo_boost}, {verified_boost}, a.name DESC"
    if promotion_type == "deal":
        return (
            f"IFNULL(a.offer_percent, 0) DESC, {geo_boost}, "
            f"{verified_boost}, a.creation DESC, a.name DESC"
        )
    return (
        f"a.average_rating DESC, a.total_reviews DESC, {geo_boost}, "
        f"{verified_boost}, a.creation DESC, a.name DESC"
    )


def _empty(limit: int, offset: int, *, cursor: str = ""):
    return ok(
        "Ads fetched.",
        data={
            "items": [],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "returned": 0,
                "next_cursor": None if cursor else "",
            },
        },
    )


def list_ads_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:ads:list:ip:{request_ip()}",
        ttl_seconds=60,
        limit=LIST_ADS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _list():
        filters = normalize_public_list_filters(kwargs)
        if filters["q"] and len(filters["q"]) < 2:
            raise AdsValidationError("Search query must contain at least two characters.", code="INVALID_SEARCH_QUERY")
        if filters["cursor"] and (filters["sort"] != "recent" or filters["offset"] != 0 or filters["q"]):
            raise AdsValidationError(
                "Cursor pagination requires recent sort, zero offset, and no search query.",
                code="INVALID_AD_CURSOR",
            )

        country, display_currency, market_error = resolve_market_context(
            country=filters["country"], currency=filters["currency"]
        )
        if market_error:
            return market_error

        user = current_user()
        today = getdate(nowdate())
        base_currency = get_aos_settings_snapshot().base_currency
        limit = filters["limit"]
        offset = filters["offset"]
        cursor = filters["cursor"]

        if filters["seller"] and not frappe.db.exists("AOS Seller", filters["seller"]):
            return _empty(limit, offset, cursor=cursor)

        candidate_ids: list[str] | None = None
        candidate_order = ""
        used_search = False
        if filters["q"]:
            try:
                candidate_ids = clean_safe_docnames(
                    search_ad_candidates(
                        q=filters["q"],
                        filters={
                            "country": country,
                            "location": filters["location"],
                            "category": filters["category"],
                            "seller": filters["seller"],
                        },
                        limit=limit,
                        offset=offset,
                    )
                )
                used_search = True
                candidate_order = _search_order_sql(candidate_ids)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "AOS Search Ranking Ads Candidate Fetch Failed")
        if used_search and not candidate_ids:
            return _empty(limit, offset, cursor=cursor)

        conditions = [
            "a.status = 'Active'",
            "s.status = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
        ]
        values: Dict[str, Any] = {
            "country": country,
            "today": today,
            "display_currency": display_currency,
            "base_currency": base_currency,
            "limit": limit,
            "offset": 0 if used_search or cursor else offset,
        }

        if user != "Guest":
            values["viewer"] = user
            conditions.append(
                """NOT EXISTS (
                    SELECT 1 FROM `tabAOS User Block` b
                    WHERE b.status = 'Active'
                      AND ((b.blocker_user = %(viewer)s AND b.blocked_user = s.user)
                        OR (b.blocker_user = s.user AND b.blocked_user = %(viewer)s))
                )"""
            )
        if filters["seller"]:
            conditions.append("a.seller = %(seller)s")
            values["seller"] = filters["seller"]
        if filters["category"]:
            category_ids = resolve_category_filter_values(filters["category"])
            if not category_ids:
                return _empty(limit, offset, cursor=cursor)
            conditions.append("a.category IN %(categories)s")
            values["categories"] = tuple(category_ids)
        if used_search and candidate_ids:
            conditions.append("a.name IN %(candidate_ids)s")
            values["candidate_ids"] = tuple(candidate_ids)
        elif filters["q"]:
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
            values["flash_end"] = add_days(today, get_aos_settings_snapshot().flash_sale_window_days)
        if filters["price_min"] is not None:
            conditions.extend([f"({original_conversion['available']}) = 1", f"{current_price_sql} >= %(price_min)s"])
            values["price_min"] = filters["price_min"]
        if filters["price_max"] is not None:
            conditions.extend([f"({original_conversion['available']}) = 1", f"{current_price_sql} <= %(price_max)s"])
            values["price_max"] = filters["price_max"]

        if cursor:
            cursor_creation, cursor_name = decode_recent_cursor(cursor)
            conditions.append(
                "(a.creation < %(cursor_creation)s OR (a.creation = %(cursor_creation)s AND a.name < %(cursor_name)s))"
            )
            values.update({"cursor_creation": cursor_creation, "cursor_name": cursor_name})

        verified_boost = "COALESCE(p.is_verified, 0) DESC"
        geo_parts = ["CASE WHEN a.country = %(country)s THEN 0 ELSE 1 END"]
        if filters["location"]:
            values["location"] = filters["location"]
            geo_parts.append("CASE WHEN a.location = %(location)s THEN 0 ELSE 1 END")
        geo_boost = ", ".join(geo_parts)

        order_by = _build_order_by(
            candidate_order=candidate_order,
            cursor=cursor,
            sort=filters["sort"],
            promotion_type=filters["promotion_type"],
            geo_boost=geo_boost,
            verified_boost=verified_boost,
            conversion_available_sql=original_conversion["available"],
            current_price_sql=current_price_sql,
        )

        where_clause = " AND ".join(conditions)
        rows = frappe.db.sql(
            f"""
            SELECT
                a.name, a.title, a.status, a.country, a.location, a.category, a.seller,
                a.currency, {original_conversion['currency']} AS display_currency,
                %(display_currency)s AS requested_display_currency,
                {original_conversion['available']} AS conversion_available,
                {original_conversion['rate']} AS conversion_rate,
                a.price_type, a.price, a.offer_price, a.offer_start_date, a.offer_end_date,
                a.offer_percent, a.price_unit, a.average_rating, a.total_reviews, a.creation,
                COALESCE(p.is_verified, 0) AS is_verified,
                {original_price_sql} AS original_price_converted,
                {current_price_sql} AS current_price
            FROM `tabAOS Ad` a
            INNER JOIN `tabAOS Seller` s ON s.name = a.seller
            INNER JOIN `tabAOS Profile` p ON p.user = s.user
            LEFT JOIN `tabAOS Exchange Rate` er_source ON er_source.currency = a.currency
            LEFT JOIN `tabAOS Exchange Rate` er_target ON er_target.currency = %(display_currency)s
            WHERE {where_clause}
            ORDER BY {order_by}
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            values,
            as_dict=True,
        )

        wishlisted = get_active_wishlist_ad_ids(user) if rows and user != "Guest" else set()
        ad_names = [row.name for row in rows]
        images_by_ad: Dict[str, List[Dict[str, Any]]] = {name: [] for name in ad_names}
        if ad_names:
            image_rows = frappe.get_all(
                "AOS Ad Image",
                filters={"parenttype": "AOS Ad", "parent": ["in", ad_names]},
                fields=["parent", "media", "image", "is_primary", "sort_order"],
                order_by="parent asc, is_primary desc, sort_order asc, name asc",
                limit=max(1, len(ad_names) * MAX_IMAGES),
            )
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
            items.append(serialize_ad_list_item(row, is_wishlisted=row.name in wishlisted))

        next_cursor = None
        if cursor and len(rows) == limit:
            last = rows[-1]
            next_cursor = encode_recent_cursor(creation=last.creation, name=last.name)
        return ok(
            "Ads fetched.",
            data={
                "items": items,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "returned": len(items),
                    "next_cursor": next_cursor,
                },
            },
        )

    return run_ads_api(_list, fallback="Failed to fetch ads.", log_title="AOS List Ads Failed")
