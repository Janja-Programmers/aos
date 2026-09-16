"""Bounded private Wishlist discovery hydrated through canonical Ads projection."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import add_to_date, getdate, now_datetime, nowdate

from aos.api.ads.category_filters import resolve_category_filter_values
from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.api.shared.sql_safety import safe_like_contains
from aos.services.ads.api import run_ads_api
from aos.services.ads.errors import AdsValidationError
from aos.services.currency_conversion import sql_conversion_expressions
from aos.services.marketplace_discovery.projection import load_public_ad_items
from aos.services.sellers.identity import resolve_public_seller_id
from aos.services.wishlist.constants import WISHLIST_LIST_LIMIT_PER_MINUTE_PER_USER
from aos.services.wishlist.validation import (
    decode_wishlist_cursor,
    encode_wishlist_cursor,
    normalize_wishlist_list_request,
)
from aos.utils.aos_settings import get_aos_settings_snapshot


def _pagination(*, limit: int, returned: int, has_more: bool, next_cursor: str | None) -> dict[str, Any]:
    return {"limit": limit, "returned": returned, "has_more": bool(has_more), "next_cursor": next_cursor}


def _empty(*, limit: int):
    return ok(
        "Wishlist fetched.",
        data={"items": [], "pagination": _pagination(limit=limit, returned=0, has_more=False, next_cursor=None)},
    )


def _order_sql(sort: str, *, conversion_available: str, current_price: str) -> str:
    if sort == "saved_oldest":
        return "w.saved_on ASC, w.name ASC"
    if sort == "price_low":
        return f"({conversion_available}) DESC, ({current_price}) ASC, w.saved_on DESC, w.name DESC"
    if sort == "price_high":
        return f"({conversion_available}) DESC, ({current_price}) DESC, w.saved_on DESC, w.name DESC"
    if sort == "rating_high":
        return "COALESCE(a.average_rating,0) DESC, COALESCE(a.total_reviews,0) DESC, w.saved_on DESC, w.name DESC"
    return "w.saved_on DESC, w.name DESC"


def _cursor_condition(sort: str, keys: dict[str, str], values: dict[str, Any], *, conversion_available: str, current_price: str) -> str:
    values["cursor_saved_on"] = keys["saved_on"]
    values["cursor_name"] = keys["name"]
    if sort == "saved_oldest":
        return "(w.saved_on > %(cursor_saved_on)s OR (w.saved_on = %(cursor_saved_on)s AND w.name > %(cursor_name)s))"
    if sort == "price_low":
        values["cursor_available"] = int(keys["available"])
        values["cursor_price"] = keys["price"]
        return f"""(
            ({conversion_available}) < %(cursor_available)s OR
            (({conversion_available}) = %(cursor_available)s AND (
                ({current_price}) > %(cursor_price)s OR
                (({current_price}) = %(cursor_price)s AND (
                    w.saved_on < %(cursor_saved_on)s OR
                    (w.saved_on = %(cursor_saved_on)s AND w.name < %(cursor_name)s)
                ))
            ))
        )"""
    if sort == "price_high":
        values["cursor_available"] = int(keys["available"])
        values["cursor_price"] = keys["price"]
        return f"""(
            ({conversion_available}) < %(cursor_available)s OR
            (({conversion_available}) = %(cursor_available)s AND (
                ({current_price}) < %(cursor_price)s OR
                (({current_price}) = %(cursor_price)s AND (
                    w.saved_on < %(cursor_saved_on)s OR
                    (w.saved_on = %(cursor_saved_on)s AND w.name < %(cursor_name)s)
                ))
            ))
        )"""
    if sort == "rating_high":
        values["cursor_rating"] = keys["rating"]
        values["cursor_reviews"] = int(keys["reviews"])
        return """(
            COALESCE(a.average_rating,0) < %(cursor_rating)s OR
            (COALESCE(a.average_rating,0) = %(cursor_rating)s AND (
                COALESCE(a.total_reviews,0) < %(cursor_reviews)s OR
                (COALESCE(a.total_reviews,0) = %(cursor_reviews)s AND (
                    w.saved_on < %(cursor_saved_on)s OR
                    (w.saved_on = %(cursor_saved_on)s AND w.name < %(cursor_name)s)
                ))
            ))
        )"""
    return "(w.saved_on < %(cursor_saved_on)s OR (w.saved_on = %(cursor_saved_on)s AND w.name < %(cursor_name)s))"


def _cursor_keys(row: Any, *, sort: str) -> dict[str, Any]:
    keys: dict[str, Any] = {"saved_on": row.wishlist_saved_on, "name": row.wishlist_id}
    if sort in {"price_low", "price_high"}:
        keys.update({"available": int(row.sort_conversion_available or 0), "price": row.sort_current_price or 0})
    elif sort == "rating_high":
        keys.update({"rating": row.sort_rating or 0, "reviews": int(row.sort_reviews or 0)})
    return keys


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
        request = normalize_wishlist_list_request(kwargs)
        country, display_currency, market_error = resolve_market_context(country=request["country"], currency=request["currency"])
        if market_error:
            return market_error

        if request["seller"]:
            request["seller"] = resolve_public_seller_id(request["seller"]) or ""
            if not request["seller"]:
                return _empty(limit=request["limit"])

        categories: list[str] = []
        if request["category"]:
            categories = resolve_category_filter_values(request["category"])
            if not categories:
                return _empty(limit=request["limit"])

        settings = get_aos_settings_snapshot()
        today = getdate(nowdate())
        values: dict[str, Any] = {
            "user": user,
            "today": today,
            "display_currency": display_currency,
            "base_currency": settings.base_currency,
            "fx_fresh_after": add_to_date(now_datetime(), hours=-settings.fx_max_stale_hours, as_string=True),
            "limit_plus_one": request["limit"] + 1,
        }
        conditions = [
            "w.user = %(user)s",
            "w.status = 'Active'",
            "a.public_id IS NOT NULL",
            "a.public_id <> ''",
            "a.status = 'Active'",
            "s.status = 'Active'",
            "u.enabled = 1",
            "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
            """NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b
                WHERE b.status='Active'
                  AND ((b.blocker_user=%(user)s AND b.blocked_user=s.user)
                    OR (b.blocker_user=s.user AND b.blocked_user=%(user)s))
            )""",
        ]
        if request["q"]:
            values["q"] = safe_like_contains(request["q"])
            conditions.append("(a.title LIKE %(q)s ESCAPE '\\\\' OR a.description LIKE %(q)s ESCAPE '\\\\')")
        if categories:
            values["categories"] = tuple(categories)
            conditions.append("a.category IN %(categories)s")
        if request["location"]:
            values["location"] = request["location"]
            conditions.append("a.location = %(location)s")
        if request["seller"]:
            values["seller"] = request["seller"]
            conditions.append("a.seller = %(seller)s")
        if request["price_type"]:
            values["price_type"] = request["price_type"]
            conditions.append("a.price_type = %(price_type)s")
        if request["rating_min"] is not None:
            values["rating_min"] = request["rating_min"]
            conditions.append("COALESCE(a.average_rating,0) >= %(rating_min)s")
        if request["verified_seller"]:
            conditions.append("COALESCE(p.is_verified,0) = 1")

        offer_active = """a.offer_price IS NOT NULL AND a.offer_price > 0
            AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
            AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)"""
        native_current = f"CASE WHEN {offer_active} THEN a.offer_price ELSE a.price END"
        current_fx = sql_conversion_expressions(amount_sql=native_current, fresh_after_param="%(fx_fresh_after)s")
        if request["price_min"] is not None:
            values["price_min"] = request["price_min"]
            conditions.extend([f"({current_fx['available']})=1", f"({current_fx['amount']}) >= %(price_min)s"])
        if request["price_max"] is not None:
            values["price_max"] = request["price_max"]
            conditions.extend([f"({current_fx['available']})=1", f"({current_fx['amount']}) <= %(price_max)s"])

        if request["cursor"]:
            keys = decode_wishlist_cursor(request["cursor"], request=request)
            required = {"saved_on", "name"}
            if request["sort"] in {"price_low", "price_high"}:
                required |= {"available", "price"}
            elif request["sort"] == "rating_high":
                required |= {"rating", "reviews"}
            if not required.issubset(keys):
                raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR")
            conditions.append(
                _cursor_condition(
                    request["sort"], keys, values,
                    conversion_available=current_fx["available"], current_price=current_fx["amount"],
                )
            )

        order_by = _order_sql(request["sort"], conversion_available=current_fx["available"], current_price=current_fx["amount"])
        rows = frappe.db.sql(
            f"""
            SELECT
                w.name AS wishlist_id,
                w.saved_on AS wishlist_saved_on,
                a.public_id,
                ({current_fx['available']}) AS sort_conversion_available,
                ({current_fx['amount']}) AS sort_current_price,
                COALESCE(a.average_rating,0) AS sort_rating,
                COALESCE(a.total_reviews,0) AS sort_reviews
            FROM `tabAOS Wishlist` w
            INNER JOIN `tabAOS Ad` a ON a.name = w.ad
            INNER JOIN `tabAOS Seller` s ON s.name = a.seller
            INNER JOIN `tabAOS Profile` p ON p.user = s.user
            INNER JOIN `tabUser` u ON u.name = s.user
            LEFT JOIN `tabAOS Exchange Rate` er_source ON er_source.currency = a.currency
            LEFT JOIN `tabAOS Exchange Rate` er_target ON er_target.currency = %(display_currency)s
            WHERE {' AND '.join(conditions)}
            ORDER BY {order_by}
            LIMIT %(limit_plus_one)s
            """,
            values,
            as_dict=True,
        )
        if not rows:
            return _empty(limit=request["limit"])

        has_more = len(rows) > request["limit"]
        page_rows = rows[: request["limit"]]
        public_ids = [str(row.public_id) for row in page_rows]
        items = load_public_ad_items(
            public_ids,
            country=country,
            currency=display_currency,
            viewer=user,
            limit=request["limit"],
            geography_rerank=False,
        )
        saved_by_public_id = {str(row.public_id): str(row.wishlist_saved_on or "") or None for row in page_rows}
        for item in items:
            item["wishlisted_on"] = saved_by_public_id.get(str(item.get("id") or ""))

        next_cursor = None
        if has_more and page_rows:
            next_cursor = encode_wishlist_cursor(
                request=request,
                keys=_cursor_keys(page_rows[-1], sort=request["sort"]),
            )
        return ok(
            "Wishlist fetched.",
            data={
                "items": items,
                "pagination": _pagination(
                    limit=request["limit"], returned=len(items), has_more=has_more, next_cursor=next_cursor
                ),
            },
        )

    return run_ads_api(_list, fallback="Failed to fetch wishlist.", log_title="AOS Wishlist List Failed")
