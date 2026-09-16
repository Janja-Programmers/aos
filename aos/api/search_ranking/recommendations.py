"""Canonical Related Ads endpoint.

Search Ranking owns relatedness candidate order. Frappe remains authoritative
for eligibility/projection and applies viewer geography as the final stable
reranking stage. A bounded database fallback preserves category discovery when
the optional ranking service is unavailable.
"""
from __future__ import annotations

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.errors import AdsNotFoundError, AdsValidationError
from aos.services.ads.validation import ensure_known_fields, normalize_identifier
from aos.services.marketplace_discovery.ids import resolve_ad_name
from aos.services.marketplace_discovery.projection import load_public_ad_items
from aos.services.search_ranking_service import SearchRankingError, related_ad_candidates

_ALLOWED = frozenset({"ad_id", "limit", "country", "currency", "location"})


def _limit(value) -> int:
    try:
        result = int(value or 20)
    except Exception as exc:
        raise AdsValidationError("Invalid related Ads limit.", code="SEARCH_INVALID_FILTERS") from exc
    if result < 1 or result > 50:
        raise AdsValidationError("Related Ads limit must be between 1 and 50.", code="SEARCH_INVALID_FILTERS")
    return result


def _source(ad_name: str):
    rows = frappe.db.sql(
        """
        SELECT a.public_id, a.category
        FROM `tabAOS Ad` a
        INNER JOIN `tabAOS Seller` s ON s.name=a.seller
        INNER JOIN `tabAOS Profile` p ON p.user=s.user
        INNER JOIN `tabUser` u ON u.name=s.user
        WHERE a.name = %s
          AND a.status = 'Active'
          AND s.status = 'Active'
          AND u.enabled = 1
          AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
          AND (a.expires_on IS NULL OR a.expires_on >= %s)
        LIMIT 1
        """,
        (ad_name, getdate(nowdate())),
        as_dict=True,
    )
    if not rows:
        raise AdsNotFoundError("Ad not found.")
    return rows[0]


def _fallback_candidates(*, category: str, exclude: str, limit: int) -> list[str]:
    return list(
        frappe.db.sql(
            """
            SELECT a.public_id
            FROM `tabAOS Ad` a
            INNER JOIN `tabAOS Seller` s ON s.name=a.seller
            INNER JOIN `tabAOS Profile` p ON p.user=s.user
            INNER JOIN `tabUser` u ON u.name=s.user
            WHERE a.status = 'Active'
              AND a.category = %s
              AND a.public_id <> %s
              AND s.status = 'Active'
              AND u.enabled = 1
              AND COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'
              AND (a.expires_on IS NULL OR a.expires_on >= %s)
            ORDER BY a.average_rating DESC, a.total_reviews DESC, a.creation DESC, a.public_id DESC
            LIMIT %s
            """,
            (category, exclude, getdate(nowdate()), min(250, max(limit * 5, 50))),
            pluck=True,
        )
    )


def related_ads_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:ads:related:ip:{request_ip()}",
        ttl_seconds=60,
        limit=300,
        message="Too many requests. Please try again shortly.",
    )
    if limited:
        return limited

    def _related():
        ensure_known_fields(kwargs, _ALLOWED)
        public_id = normalize_identifier(kwargs.get("ad_id"), field="ad_id", required=True)
        limit = _limit(kwargs.get("limit"))
        location = normalize_identifier(kwargs.get("location"), field="location", max_length=140)
        country, currency, market_error = resolve_market_context(
            country=kwargs.get("country"), currency=kwargs.get("currency")
        )
        if market_error:
            return market_error
        ad_name = resolve_ad_name(public_id)
        source = _source(ad_name)
        try:
            candidates = related_ad_candidates(ad_id=public_id, limit=min(100, max(limit * 5, 50)))
        except SearchRankingError:
            frappe.log_error(frappe.get_traceback(), "AOS Related Ads Ranking Degraded")
            candidates = _fallback_candidates(category=source.category, exclude=public_id, limit=limit)
        items = load_public_ad_items(
            candidates,
            country=country or "",
            currency=currency or "",
            location=location,
            viewer=current_user(),
            limit=limit,
            exclude_public_id=public_id,
            required_category=source.category,
        )
        return ok("Related ads fetched.", data={"items": items})

    return run_ads_api(_related, fallback="Failed to fetch related ads.", log_title="AOS Related Ads Failed")
