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
from aos.services.marketplace_discovery.projection import load_public_ad_items, public_ad_sql_context
from aos.services.sellers.identity import normalize_public_seller_id
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

        # Cursor payloads are untrusted request data. Decode and validate them
        # before market/settings/catalog/database reads so malformed cursors
        # are rejected without touching listing SQL or side-effect logging.
        cursor_keys: dict[str, str] | None = None
        if request["cursor"]:
            cursor_keys = decode_wishlist_cursor(request["cursor"], request=request)
            required = {"saved_on", "name"}
            if request["sort"] in {"price_low", "price_high"}:
                required |= {"available", "price"}
            elif request["sort"] == "rating_high":
                required |= {"rating", "reviews"}
            if not required.issubset(cursor_keys):
                raise AdsValidationError("Invalid wishlist pagination cursor.", code="INVALID_WISHLIST_CURSOR")

        country, display_currency, market_error = resolve_market_context(country=request["country"], currency=request["currency"])
        if market_error:
            return market_error

        seller_public_id = ""
        if request["seller"]:
            seller_public_id = normalize_public_seller_id(request["seller"])
            if not seller_public_id:
                return _empty(limit=request["limit"])

        categories: list[str] = []
        if request["category"]:
            categories = resolve_category_filter_values(request["category"])
            if not categories:
                return _empty(limit=request["limit"])

        needs_fx = bool(
            request["sort"] in {"price_low", "price_high"}
            or request["price_min"] is not None
            or request["price_max"] is not None
        )
        sql_context = public_ad_sql_context(viewer_param="user", include_fx=needs_fx)
        today = getdate(nowdate())
        values: dict[str, Any] = {
            "user": user,
            "today": today,
            "limit_plus_one": request["limit"] + 1,
        }
        if needs_fx:
            settings = get_aos_settings_snapshot()
            values.update(
                {
                    "display_currency": display_currency,
                    "base_currency": settings.base_currency,
                    "fx_fresh_after": add_to_date(
                        now_datetime(),
                        hours=-settings.fx_max_stale_hours,
                        as_string=True,
                    ),
                }
            )
        conditions = [
            "w.user = %(user)s",
            "w.status = 'Active'",
            "a.public_id IS NOT NULL",
            "a.public_id <> ''",
            *sql_context.conditions,
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
        if seller_public_id:
            values["seller_public_id"] = seller_public_id
            conditions.append("s.public_id = %(seller_public_id)s")
        if request["price_type"]:
            values["price_type"] = request["price_type"]
            conditions.append("a.price_type = %(price_type)s")
        if request["rating_min"] is not None:
            values["rating_min"] = request["rating_min"]
            conditions.append("COALESCE(a.average_rating,0) >= %(rating_min)s")
        if request["verified_seller"]:
            conditions.append("COALESCE(p.is_verified,0) = 1")

        current_fx = sql_context.current_fx
        if request["price_min"] is not None:
            assert current_fx is not None
            values["price_min"] = request["price_min"]
            conditions.extend([f"({current_fx['available']})=1", f"({current_fx['amount']}) >= %(price_min)s"])
        if request["price_max"] is not None:
            assert current_fx is not None
            values["price_max"] = request["price_max"]
            conditions.extend([f"({current_fx['available']})=1", f"({current_fx['amount']}) <= %(price_max)s"])

        conversion_available = current_fx["available"] if current_fx is not None else "1"
        current_price = current_fx["amount"] if current_fx is not None else "0"
        if cursor_keys is not None:
            conditions.append(
                _cursor_condition(
                    request["sort"], cursor_keys, values,
                    conversion_available=conversion_available,
                    current_price=current_price,
                )
            )

        order_by = _order_sql(
            request["sort"],
            conversion_available=conversion_available,
            current_price=current_price,
        )
        rows = frappe.db.sql(
            f"""
            SELECT
                w.name AS wishlist_id,
                w.saved_on AS wishlist_saved_on,
                a.public_id,
                ({conversion_available}) AS sort_conversion_available,
                ({current_price}) AS sort_current_price,
                COALESCE(a.average_rating,0) AS sort_rating,
                COALESCE(a.total_reviews,0) AS sort_reviews
            FROM `tabAOS Wishlist` w
            INNER JOIN `tabAOS Ad` a ON a.name = w.ad
            {' '.join(sql_context.joins)}
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
