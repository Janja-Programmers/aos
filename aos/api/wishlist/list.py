"""Bounded private Wishlist listing hydrated through canonical Ads projection."""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.marketplace_discovery.projection import load_public_ad_items
from aos.services.wishlist.constants import WISHLIST_LIST_LIMIT_PER_MINUTE_PER_USER
from aos.services.wishlist.validation import (
    WISHLIST_LIST_MAX_SCAN,
    WISHLIST_LIST_SCAN_MULTIPLIER,
    decode_wishlist_cursor,
    encode_wishlist_cursor,
    normalize_wishlist_list_request,
)


def _pagination(*, limit: int, returned: int, has_more: bool, next_cursor: str | None) -> dict[str, Any]:
    return {
        "limit": limit,
        "returned": returned,
        "has_more": bool(has_more),
        "next_cursor": next_cursor,
    }


def _empty(*, limit: int):
    return ok(
        "Wishlist fetched.",
        data={
            "items": [],
            "pagination": _pagination(
                limit=limit,
                returned=0,
                has_more=False,
                next_cursor=None,
            ),
        },
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
        request = normalize_wishlist_list_request(kwargs)
        cursor_values = decode_wishlist_cursor(request["cursor"]) if request["cursor"] else None
        country, display_currency, market_error = resolve_market_context(
            country=request["country"],
            currency=request["currency"],
        )
        if market_error:
            return market_error

        limit = request["limit"]
        scan_limit = min(WISHLIST_LIST_MAX_SCAN, max(limit, limit * WISHLIST_LIST_SCAN_MULTIPLIER))
        values: dict[str, Any] = {
            "user": user,
            "scan_limit": scan_limit + 1,
        }
        saved_on_sql = "w.saved_on"
        conditions = [
            "w.user = %(user)s",
            "w.status = 'Active'",
            "a.public_id IS NOT NULL",
            "a.public_id <> ''",
        ]
        if cursor_values:
            cursor_saved_on, cursor_name = cursor_values
            conditions.append(
                f"({saved_on_sql} < %(cursor_saved_on)s OR "
                f"({saved_on_sql} = %(cursor_saved_on)s AND w.name < %(cursor_name)s))"
            )
            values["cursor_saved_on"] = cursor_saved_on
            values["cursor_name"] = cursor_name

        rows = frappe.db.sql(
            f"""
            SELECT
                w.name AS wishlist_id,
                {saved_on_sql} AS wishlist_saved_on,
                a.public_id
            FROM `tabAOS Wishlist` w
            INNER JOIN `tabAOS Ad` a ON a.name = w.ad
            WHERE {' AND '.join(conditions)}
            ORDER BY {saved_on_sql} DESC, w.name DESC
            LIMIT %(scan_limit)s
            """,
            values,
            as_dict=True,
        )
        if not rows:
            return _empty(limit=limit)

        more_relationships = len(rows) > scan_limit
        scanned = rows[:scan_limit]
        public_ids = [str(row.public_id) for row in scanned]
        items = load_public_ad_items(
            public_ids,
            country=country,
            currency=display_currency,
            viewer=user,
            limit=limit,
            geography_rerank=False,
        )

        saved_by_public_id = {
            str(row.public_id): str(row.wishlist_saved_on or "") or None
            for row in scanned
        }
        row_by_public_id = {str(row.public_id): row for row in scanned}
        for item in items:
            item["wishlisted_on"] = saved_by_public_id.get(str(item.get("id") or ""))

        cursor_row = None
        has_more = False
        if len(items) >= limit and items:
            cursor_row = row_by_public_id.get(str(items[-1].get("id") or ""))
            if cursor_row:
                cursor_index = next(
                    (
                        index
                        for index, row in enumerate(scanned)
                        if str(row.wishlist_id) == str(cursor_row.wishlist_id)
                    ),
                    len(scanned) - 1,
                )
                has_more = more_relationships or cursor_index < len(scanned) - 1
        elif more_relationships:
            # All visible items from this bounded scan were returned. Advance
            # past hidden/unavailable rows so a client cannot loop forever on
            # stale private relationships.
            cursor_row = scanned[-1]
            has_more = True

        next_cursor = None
        if has_more and cursor_row:
            next_cursor = encode_wishlist_cursor(
                saved_on=cursor_row.wishlist_saved_on,
                name=cursor_row.wishlist_id,
            )

        return ok(
            "Wishlist fetched.",
            data={
                "items": items,
                "pagination": _pagination(
                    limit=limit,
                    returned=len(items),
                    has_more=has_more,
                    next_cursor=next_cursor,
                ),
            },
        )

    return run_ads_api(
        _list,
        fallback="Failed to fetch wishlist.",
        log_title="AOS Wishlist List Failed",
    )
