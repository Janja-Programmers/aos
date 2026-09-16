"""Canonical public Ads discovery query.

Candidate generation/relevance happens first. Canonical DB eligibility and
filters are then applied. Geographic locality is the final stable reranking
stage (exact location -> country -> global), followed by deterministic ties.
"""
from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import add_days, add_to_date, getdate, now_datetime, nowdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.api.shared.sql_safety import clean_safe_docnames, safe_like_contains
from aos.api.shared.utils import get_active_wishlist_ad_ids
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import MAX_IMAGES
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import decode_recent_cursor, encode_recent_cursor, normalize_public_list_filters
from aos.services.currency_conversion import sql_conversion_expressions
from aos.services.marketplace_discovery.geography import sql_geographic_bucket
from aos.services.media.media_service import MediaService
from aos.services.search_ranking_service import search_ad_candidates
from aos.services.sellers.identity import resolve_public_seller_id
from aos.utils.aos_settings import get_aos_settings_snapshot

from .category_filters import resolve_category_filter_values
from .constants import LIST_ADS_LIMIT_PER_MINUTE_PER_IP
from .serializers import serialize_ad_list_item


def _search_order_sql(public_ids: list[str]) -> str:
    safe_ids = clean_safe_docnames(public_ids)
    if not safe_ids:
        return ""
    escaped = ", ".join(frappe.db.escape(value) for value in safe_ids)
    return f"FIELD(a.public_id, {escaped})"


def _primary_order(*, candidate_order: str, sort: str, promotion_type: str | None, conversion_available: str, price: str, verified: str) -> str:
    if sort == "price_low":
        return f"{conversion_available} DESC, {price} ASC, {verified}, a.creation DESC, a.public_id DESC"
    if sort == "price_high":
        return f"{conversion_available} DESC, {price} DESC, {verified}, a.creation DESC, a.public_id DESC"
    if sort == "recent":
        return "a.creation DESC, a.public_id DESC"
    if candidate_order:
        return f"{candidate_order} ASC, {verified}, a.creation DESC, a.public_id DESC"
    if promotion_type == "deal":
        return f"IFNULL(a.offer_percent, 0) DESC, {verified}, a.creation DESC, a.public_id DESC"
    return f"a.average_rating DESC, a.total_reviews DESC, {verified}, a.creation DESC, a.public_id DESC"


def _attribute_conditions(attributes: list[dict[str, Any]], values: dict[str, Any]) -> list[str]:
    conditions: list[str] = []
    for index, item in enumerate(attributes):
        prefix = f"attr_{index}"
        values[f"{prefix}_id"] = item["attribute"]
        attr_type = item["type"]
        value = item["value"]
        clauses = [f"av.attribute = %({prefix}_id)s"]
        if attr_type in {"Text", "Textarea", "Select", "Year"}:
            values[f"{prefix}_value"] = value
            clauses.append(f"av.value_text = %({prefix}_value)s")
        elif attr_type == "Number":
            values[f"{prefix}_value"] = value
            clauses.append(f"av.value_number = %({prefix}_value)s")
        elif attr_type == "Boolean":
            values[f"{prefix}_value"] = 1 if value else 0
            clauses.append(f"av.value_bool = %({prefix}_value)s")
        elif attr_type == "Date":
            values[f"{prefix}_value"] = value
            clauses.append(f"av.value_date = %({prefix}_value)s")
        elif attr_type == "MultiSelect":
            for value_index, selected in enumerate(value):
                key = f"{prefix}_value_{value_index}"
                values[key] = selected
                clauses.append(f"JSON_CONTAINS(COALESCE(av.value_json, '[]'), JSON_QUOTE(%({key})s))")
        conditions.append(
            "EXISTS (SELECT 1 FROM `tabAOS Ad Attribute Value` av "
            "WHERE av.parenttype='AOS Ad' AND av.parent=a.name AND " + " AND ".join(clauses) + ")"
        )
    return conditions


def _empty(limit: int, offset: int):
    return ok("Ads fetched.", data={"items": [], "pagination": {"limit": limit, "offset": offset, "returned": 0, "next_cursor": None}})


def list_ads_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:ads:list:ip:{request_ip()}", ttl_seconds=60,
        limit=LIST_ADS_LIMIT_PER_MINUTE_PER_IP, message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _list():
        filters = normalize_public_list_filters(kwargs)
        if filters["cursor"] and (filters["sort"] != "recent" or filters["offset"] != 0 or filters["q"]):
            raise AdsValidationError("Cursor pagination requires recent sort, zero offset, and no search query.", code="INVALID_AD_CURSOR")

        if filters["seller"]:
            filters["seller"] = resolve_public_seller_id(filters["seller"]) or ""
            if not filters["seller"]:
                return _empty(filters["limit"], filters["offset"])

        country, display_currency, market_error = resolve_market_context(country=filters["country"], currency=filters["currency"])
        if market_error:
            return market_error
        location = filters["location"] or ""
        viewer = current_user()
        today = getdate(nowdate())
        settings = get_aos_settings_snapshot()
        limit, offset = filters["limit"], filters["offset"]
        geo_bucket = sql_geographic_bucket(table_alias="a")

        values: Dict[str, Any] = {
            "country": country or "", "location": location, "today": today,
            "display_currency": display_currency, "base_currency": settings.base_currency,
            "fx_fresh_after": add_to_date(now_datetime(), hours=-settings.fx_max_stale_hours, as_string=True),
            "limit": limit, "offset": offset,
        }
        conditions = [
            "a.status = 'Active'", "a.public_id IS NOT NULL", "a.public_id <> ''",
            "s.status = 'Active'", "u.enabled = 1",
            "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
        ]
        if viewer != "Guest":
            values["viewer"] = viewer
            conditions.append("""NOT EXISTS (
                SELECT 1 FROM `tabAOS User Block` b WHERE b.status='Active'
                AND ((b.blocker_user=%(viewer)s AND b.blocked_user=s.user)
                  OR (b.blocker_user=s.user AND b.blocked_user=%(viewer)s))
            )""")
        if filters["seller"]:
            conditions.append("a.seller = %(seller)s"); values["seller"] = filters["seller"]
        categories: list[str] = []
        if filters["category"]:
            categories = resolve_category_filter_values(filters["category"])
            if not categories:
                return _empty(limit, offset)
            conditions.append("a.category IN %(categories)s"); values["categories"] = tuple(categories)
        conditions.extend(_attribute_conditions(filters.get("attributes") or [], values))

        candidate_ids: list[str] = []
        candidate_order = ""
        if filters["q"]:
            try:
                # Geography is intentionally absent here: it is the final rerank,
                # not a candidate filter. Pull bounded headroom before reranking.
                candidate_scan = min(500, max(100, offset + limit * 5))
                candidate_ids = clean_safe_docnames(search_ad_candidates(
                    q=filters["q"],
                    filters={
                        "categories": categories, "seller": filters["seller"],
                        "attributes": {row["key"]: row["value"] for row in filters.get("attributes") or []},
                    },
                    limit=candidate_scan, offset=0,
                ))
                if not candidate_ids:
                    return _empty(limit, offset)
                conditions.append("a.public_id IN %(candidate_ids)s")
                values["candidate_ids"] = tuple(candidate_ids)
                candidate_order = _search_order_sql(candidate_ids)
            except Exception:
                frappe.log_error(frappe.get_traceback(), "AOS Search Ranking Candidate Fetch Failed")
                conditions.append("a.title LIKE %(q)s ESCAPE '\\\\'")
                values["q"] = safe_like_contains(filters["q"])

        if filters["price_type"]:
            conditions.append("a.price_type = %(price_type)s"); values["price_type"] = filters["price_type"]
        if filters["rating_min"] is not None:
            conditions.append("a.average_rating >= %(rating_min)s"); values["rating_min"] = filters["rating_min"]
        if filters["verified_seller"]:
            conditions.append("COALESCE(p.is_verified, 0) = 1")

        offer_active = """a.offer_price IS NOT NULL AND a.offer_price > 0
            AND (a.offer_start_date IS NULL OR a.offer_start_date <= %(today)s)
            AND (a.offer_end_date IS NULL OR a.offer_end_date >= %(today)s)"""
        native_current = f"CASE WHEN {offer_active} THEN a.offer_price ELSE a.price END"
        original_fx = sql_conversion_expressions(amount_sql="a.price", fresh_after_param="%(fx_fresh_after)s")
        current_fx = sql_conversion_expressions(amount_sql=native_current, fresh_after_param="%(fx_fresh_after)s")
        if filters["promotion_type"] in {"offer", "flash_sale", "deal"}:
            conditions.append(f"({offer_active})")
        if filters["promotion_type"] == "flash_sale":
            conditions.append("a.offer_end_date BETWEEN %(today)s AND %(flash_end)s")
            values["flash_end"] = add_days(today, settings.flash_sale_window_days)
        if filters["price_min"] is not None:
            conditions.extend([f"({current_fx['available']})=1", f"{current_fx['amount']} >= %(price_min)s"]); values["price_min"] = filters["price_min"]
        if filters["price_max"] is not None:
            conditions.extend([f"({current_fx['available']})=1", f"{current_fx['amount']} <= %(price_max)s"]); values["price_max"] = filters["price_max"]

        primary = _primary_order(
            candidate_order=candidate_order, sort=filters["sort"], promotion_type=filters["promotion_type"],
            conversion_available=original_fx["available"], price=current_fx["amount"], verified="COALESCE(p.is_verified,0) DESC",
        )

        if filters["cursor"]:
            cursor_bucket, cursor_creation, cursor_public_id = decode_recent_cursor(filters["cursor"])
            values.update({"cursor_bucket": cursor_bucket, "cursor_creation": cursor_creation, "cursor_public_id": cursor_public_id})
            conditions.append(
                f"(({geo_bucket}) > %(cursor_bucket)s OR (({geo_bucket}) = %(cursor_bucket)s AND "
                "(a.creation < %(cursor_creation)s OR (a.creation=%(cursor_creation)s AND a.public_id < %(cursor_public_id)s))))"
            )
            values["offset"] = 0

        order_by = f"{geo_bucket} ASC, {primary}"
        rows = frappe.db.sql(
            f"""
            SELECT a.name, a.public_id, a.title, a.status, a.country, a.location, loc.location AS location_name, a.category, a.seller,
                   a.currency, {original_fx['currency']} AS display_currency,
                   %(display_currency)s AS requested_display_currency,
                   {original_fx['available']} AS conversion_available,
                   {original_fx['rate']} AS conversion_rate,
                   a.price_type, a.price, a.offer_price, a.offer_start_date, a.offer_end_date,
                   a.offer_percent, a.price_unit, a.average_rating, a.total_reviews, a.creation,
                   {geo_bucket} AS geo_bucket,
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
            ORDER BY {order_by}
            LIMIT %(limit)s OFFSET %(offset)s
            """, values, as_dict=True,
        )

        names = [row.name for row in rows]
        wishlisted = get_active_wishlist_ad_ids(viewer, ad_ids=names) if names and viewer != "Guest" else set()
        images: Dict[str, List[Dict[str, Any]]] = {name: [] for name in names}
        if names:
            image_rows = frappe.get_all(
                "AOS Ad Image", filters={"parenttype":"AOS Ad", "parent":["in", names]},
                fields=["parent","media","is_primary","sort_order"],
                order_by="parent asc, is_primary desc, sort_order asc, name asc", limit=max(1, len(names)*MAX_IMAGES),
            )
            attachments = [(str(image.media or "").strip(), image.parent) for image in image_rows if str(image.media or "").strip()]
            urls = MediaService().get_public_attachment_url_map(
                attachments, purpose="ad_image", attached_doctype="AOS Ad", attached_field="images"
            ) if attachments else {}
            for image in image_rows:
                item = dict(image)
                item["url"] = urls.get((str(image.media or "").strip(), image.parent), "")
                images.setdefault(image.parent, []).append(item)
        items = []
        for row in rows:
            row.images = images.get(row.name, [])
            row.is_offer_active = bool(row.offer_price and (row.offer_start_date is None or row.offer_start_date <= today) and (row.offer_end_date is None or row.offer_end_date >= today))
            items.append(serialize_ad_list_item(row, is_wishlisted=row.name in wishlisted))

        next_cursor = None
        if filters["sort"] == "recent" and len(rows) == limit:
            last = rows[-1]
            next_cursor = encode_recent_cursor(geo_bucket=int(last.geo_bucket), creation=last.creation, public_id=last.public_id)
        return ok("Ads fetched.", data={"items":items, "pagination":{"limit":limit,"offset":offset,"returned":len(items),"next_cursor":next_cursor}})

    return run_ads_api(_list, fallback="Failed to fetch ads.", log_title="AOS List Ads Failed")
