"""Privacy-safe public Ad detail."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import getdate, nowdate

from aos.api.shared.auth import current_user
from aos.api.shared.market_context import resolve_market_context
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import GET_AD_FIELDS, MAX_AD_ATTRIBUTES, MAX_IMAGES
from aos.services.ads.errors import AdsNotFoundError
from aos.services.ads.validation import ensure_known_fields, normalize_identifier
from aos.services.analytics_pipeline_service import emit_analytics_event
from aos.services.currency_conversion import convert_amount
from aos.utils.aos_settings import get_aos_settings_snapshot

from .activity import record_ad_view_activity
from .constants import GET_AD_LIMIT_PER_HOUR_PER_IP
from .serializers import serialize_ad_detail


def _rate_for(currency: str, *, base_currency: str) -> Any:
    """Return the configured rate, with the base currency represented by one."""
    if currency == base_currency:
        return 1
    return frappe.db.get_value("AOS Exchange Rate", currency, "rate_vs_base")


def _is_offer_active(ad_doc: Any, *, today: Any) -> bool:
    return bool(
        ad_doc.offer_price
        and (ad_doc.offer_start_date is None or ad_doc.offer_start_date <= today)
        and (ad_doc.offer_end_date is None or ad_doc.offer_end_date >= today)
    )


def _apply_display_price(
    ad_doc: Any,
    *,
    requested_currency: str,
    base_currency: str,
    today: Any,
) -> None:
    """Attach viewer-currency metadata without mutating authoritative prices."""
    source_currency = str(ad_doc.currency or "").strip()
    source_rate = _rate_for(source_currency, base_currency=base_currency)
    target_rate = _rate_for(requested_currency, base_currency=base_currency)
    offer_active = _is_offer_active(ad_doc, today=today)
    native_current_price = ad_doc.offer_price if offer_active else ad_doc.price

    original = convert_amount(
        ad_doc.price or 0,
        source_currency,
        requested_currency,
        source_rate,
        target_rate,
        base_currency,
    )
    current = convert_amount(
        native_current_price or 0,
        source_currency,
        requested_currency,
        source_rate,
        target_rate,
        base_currency,
    )

    ad_doc.display_currency = original.display_currency
    ad_doc.requested_display_currency = original.requested_display_currency
    ad_doc.conversion_available = int(original.available)
    ad_doc.conversion_rate = original.rate
    ad_doc.original_price_converted = original.amount
    ad_doc.current_price = current.amount
    ad_doc.is_offer_active = offer_active


def _viewer_has_wishlisted(*, viewer: str, ad_id: str) -> bool:
    if viewer == "Guest":
        return False
    return bool(
        frappe.db.exists(
            "AOS Wishlist",
            {"user": viewer, "ad": ad_id, "status": "Active"},
        )
    )


def _record_view_best_effort(*, viewer: str, ad_id: str) -> None:
    if viewer == "Guest":
        return
    try:
        record_ad_view_activity(user=viewer, ad_id=ad_id)
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Ad View Activity Failed")


def get_ad_impl(**kwargs):
    limited = rate_limit(
        key=f"aos:ads:get:ip:{request_ip()}",
        ttl_seconds=60 * 60,
        limit=GET_AD_LIMIT_PER_HOUR_PER_IP,
        message="Too many requests. Please try again later.",
    )
    if limited:
        return limited

    def _get():
        ensure_known_fields(kwargs, GET_AD_FIELDS)
        ad_id = normalize_identifier(
            kwargs.get("ad_id") or kwargs.get("id"),
            field="ad_id",
            required=True,
        )
        _country, display_currency, market_error = resolve_market_context(
            country=kwargs.get("country"),
            currency=kwargs.get("currency"),
        )
        if market_error:
            return market_error

        viewer = current_user()
        today = getdate(nowdate())
        conditions = [
            "a.name = %(ad_id)s",
            "a.status = 'Active'",
            "s.status = 'Active'",
            "u.enabled = 1",
            "COALESCE(NULLIF(p.account_status, ''), 'Active') = 'Active'",
            "(a.expires_on IS NULL OR a.expires_on >= %(today)s)",
        ]
        values: dict[str, Any] = {"ad_id": ad_id, "today": today}
        if viewer != "Guest":
            values["viewer"] = viewer
            conditions.append(
                """NOT EXISTS (
                    SELECT 1 FROM `tabAOS User Block` b
                    WHERE b.status = 'Active'
                      AND ((b.blocker_user = %(viewer)s AND b.blocked_user = s.user)
                        OR (b.blocker_user = s.user AND b.blocked_user = %(viewer)s))
                )"""
            )

        rows = frappe.db.sql(
            f"""
            SELECT a.name
            FROM `tabAOS Ad` a
            INNER JOIN `tabAOS Seller` s ON s.name = a.seller
            INNER JOIN `tabAOS Profile` p ON p.user = s.user
            INNER JOIN `tabUser` u ON u.name = s.user
            WHERE {' AND '.join(conditions)}
            LIMIT 1
            """,
            values,
            as_dict=True,
        )
        if not rows:
            raise AdsNotFoundError("Ad not found.")

        ad_doc = frappe.get_doc("AOS Ad", ad_id)
        ad_doc.images = list(ad_doc.images or [])[:MAX_IMAGES]
        ad_doc.details = list(ad_doc.details or [])[:MAX_AD_ATTRIBUTES]
        settings = get_aos_settings_snapshot()
        _apply_display_price(
            ad_doc,
            requested_currency=display_currency,
            base_currency=settings.base_currency,
            today=today,
        )
        item = serialize_ad_detail(
            ad_doc,
            is_wishlisted=_viewer_has_wishlisted(viewer=viewer, ad_id=ad_id),
        )

        _record_view_best_effort(viewer=viewer, ad_id=ad_id)
        try:
            emit_analytics_event(
                event_type="ad_view",
                event_group="ads",
                user=viewer if viewer != "Guest" else None,
                target_doctype="AOS Ad",
                target_name=ad_id,
                route_type="ad",
                route_id=ad_id,
                source="ads.get_ad",
                country=ad_doc.country,
                metadata={
                    "category": ad_doc.category,
                    "seller": ad_doc.seller,
                    "location": ad_doc.location,
                },
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Ad View Analytics Emit Failed")
        return ok("Ad fetched.", data={"item": item})

    return run_ads_api(
        _get,
        fallback="Failed to fetch ad.",
        log_title="AOS Get Ad Failed",
    )
