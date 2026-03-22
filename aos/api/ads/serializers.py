"""
Ads serializers.

The mobile app needs four shapes:
1) Buyer List item
2) Buyer Detail
3) Seller My Ads item
4) Seller Edit item
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe
from aos.api.catalog.schema import _attribute_key

# Currency symbol cache
_currency_symbol_cache: Dict[str, str] = {}


def _get_currency_symbol(code: str) -> str:
    code = str(code or "").strip()

    if not code:
        return ""

    if code in _currency_symbol_cache:
        return _currency_symbol_cache[code]

    symbol = frappe.db.get_value("Currency", code, "symbol") or ""
    symbol = str(symbol).strip()

    _currency_symbol_cache[code] = symbol

    return symbol


# Helpers
def _norm(value: Any) -> str:
    return str(value or "").strip()


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None

    try:
        return float(value)
    except Exception:
        return None


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


# Price formatting
def _money_display(currency: str, price: Any, price_type: str) -> str:
    price_type = _norm(price_type)

    if price_type == "Contact for price":
        return "Contact for price"

    if price_type == "Free":
        return "Free"

    amount = _to_float(price)

    if amount is None:
        return ""

    currency_code = _norm(currency)

    symbol = _get_currency_symbol(currency_code)

    if symbol:
        return f"{symbol} {amount:,.2f}"

    if currency_code:
        return f"{currency_code} {amount:,.2f}"

    return f"{amount:,.2f}"


# Primary image
def _primary_image(images: List[Dict[str, Any]]) -> str:
    for image in images:
        if _to_int(image.get("is_primary")) == 1 and _norm(image.get("image")):
            return _norm(image.get("image"))

    for image in images:
        if _norm(image.get("image")):
            return _norm(image.get("image"))

    return ""


# Images serializer
def serialize_ad_images(ad_doc) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for row in (getattr(ad_doc, "images", []) or []):
        items.append(
            {
                "image": _norm(getattr(row, "image", None)),
                "is_primary": _to_int(getattr(row, "is_primary", 0)),
                "sort_order": _to_int(getattr(row, "sort_order", 0)),
            }
        )

    items.sort(
        key=lambda x: (
            0 if x["is_primary"] else 1,
            x["sort_order"],
            x["image"],
        )
    )

    return items


# Details serializer
def serialize_ad_details(ad_doc) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for row in (getattr(ad_doc, "details", []) or []):
        items.append(
            {
                "attribute": _norm(getattr(row, "attribute", None)),
                "value_text": getattr(row, "value_text", None),
                "value_number": getattr(row, "value_number", None),
                "value_date": getattr(row, "value_date", None),
                "value_bool": _to_int(getattr(row, "value_bool", 0)),
                "value_json": getattr(row, "value_json", None),
            }
        )

    return items


# Buyer List Serializer
def serialize_ad_list_item(ad_doc, is_wishlisted: bool = False) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)

    display_currency = _norm(
        getattr(ad_doc, "display_currency", getattr(ad_doc, "currency", None))
    )

    original_price = _to_float(getattr(ad_doc, "original_price_converted", None))
    current_price = _to_float(getattr(ad_doc, "current_price", None))

    price_type = _norm(getattr(ad_doc, "price_type", None))
    price_unit = _norm(getattr(ad_doc, "price_unit", None))

    is_offer_active = bool(getattr(ad_doc, "is_offer_active", False))

    original_price_display = _money_display(
        display_currency,
        original_price,
        price_type,
    )

    current_price_display = _money_display(
        display_currency,
        current_price,
        price_type,
    )

    if not is_offer_active:
        original_price_display = ""

    return {
        "id": ad_doc.name,
        "title": _norm(getattr(ad_doc, "title", None)),
        "status": _norm(getattr(ad_doc, "status", None)),
        "country": _norm(getattr(ad_doc, "country", None)),
        "location": _norm(getattr(ad_doc, "location", None)),
        "category": _norm(getattr(ad_doc, "category", None)),
        "current_price": current_price_display,
        "original_price": original_price_display,
        "offer_percent": _to_float(getattr(ad_doc, "offer_percent", None))
        if is_offer_active
        else 0,
        "is_offer_active": is_offer_active,
        "price_type": price_type,
        "price_unit": price_unit,
        "primary_image": _primary_image(images),
        "created_at": getattr(ad_doc, "creation", None),
        "is_wishlisted": bool(is_wishlisted),
        "average_rating": _to_float(getattr(ad_doc, "average_rating", None)),
        "total_reviews": _to_int(getattr(ad_doc, "total_reviews", 0)),
    }


# Buyer Detail Serializer
def serialize_ad_detail(ad_doc, is_wishlisted: bool = False) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)

    return {
        **serialize_ad_list_item(ad_doc, is_wishlisted=is_wishlisted),
        "seller": _norm(getattr(ad_doc, "seller", None)),
        "description": getattr(ad_doc, "description", None) or "",
        "video": _norm(getattr(ad_doc, "video", None)),
        "images": images,
        "details": serialize_ad_details(ad_doc),
    }


# Seller My Ads Serializer
def serialize_my_ad_list_item(ad_doc) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)

    current_price = _to_float(getattr(ad_doc, "current_price", None))

    price_display = _money_display(
        _norm(getattr(ad_doc, "currency", None)),
        current_price,
        getattr(ad_doc, "price_type", None),
    )

    return {
        "id": ad_doc.name,
        "title": _norm(getattr(ad_doc, "title", None)),
        "status": _norm(getattr(ad_doc, "status", None)),
        "country": _norm(getattr(ad_doc, "country", None)),
        "location": _norm(getattr(ad_doc, "location", None)),
        "current_price": price_display,
        "primary_image": _primary_image(images),
        "created_at": getattr(ad_doc, "creation", None),
    }


# Seller Edit Serializer
def serialize_ad_for_edit(ad_doc) -> Dict[str, Any]:
    images = []
    for row in (getattr(ad_doc, "images", []) or []):
        images.append(
            {
                "image": row.image,
                "is_primary": row.is_primary,
                "sort_order": row.sort_order,
            }
        )

    details = []
    for row in (getattr(ad_doc, "details", []) or []):
        details.append(
            {
                "attribute": _attribute_key(row.attribute),
                "value_text": row.value_text,
                "value_number": row.value_number,
                "value_date": row.value_date,
                "value_bool": row.value_bool,
                "value_json": row.value_json,
            }
        )

    return {
        "id": ad_doc.name,
        "title": ad_doc.title,
        "status": ad_doc.status,
        "location": ad_doc.location,
        "category": ad_doc.category,
        "images": images,
        "video": ad_doc.video,
        "details": details,
        "description": ad_doc.description,
        "price": ad_doc.price,
        "price_type": ad_doc.price_type,
        "price_unit": ad_doc.price_unit,
        "offer_price": ad_doc.offer_price,
        "offer_start_date": ad_doc.offer_start_date,
        "offer_end_date": ad_doc.offer_end_date,
    }
