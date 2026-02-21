"""
Ads serializers.

The mobile app needs two shapes:
1) List item (lightweight)
2) Detail (full fields + children)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe


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


def _money_display(currency: str, price: Any) -> str:
    """
    Human display for CURRENT price.
    """
    currency_code = _norm(currency)
    amount = _to_float(price)

    if amount is None:
        return ""

    symbol = None

    if currency_code:
        try:
            symbol = frappe.db.get_value(
                "Currency",
                currency_code,
                "symbol",
            )
        except Exception:
            symbol = None

    symbol = _norm(symbol)

    if symbol:
        return f"{symbol}{amount:g}"

    # Fallback to currency code if symbol missing
    if currency_code:
        return f"{currency_code} {amount:g}"
    return f"{amount:g}"


def _primary_image(images: List[Dict[str, Any]]) -> str:
    for image in images:
        if _to_int(image.get("is_primary")) == 1 and _norm(image.get("image")):
            return _norm(image.get("image"))

    for image in images:
        if _norm(image.get("image")):
            return _norm(image.get("image"))

    return ""

# Images

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

# Details

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

# List Item

def serialize_ad_list_item(ad_doc, is_wishlisted: bool = False) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)
    base_price = getattr(ad_doc, "price", None)
    current_price = getattr(ad_doc, "current_price", base_price)
    offer_price = getattr(ad_doc, "offer_price", None)

    return {
        "id": ad_doc.name,
        "title": _norm(getattr(ad_doc, "title", None)),
        "status": _norm(getattr(ad_doc, "status", None)),
        "country": _norm(getattr(ad_doc, "country", None)),
        "location": _norm(getattr(ad_doc, "location", None)),
        "category": _norm(getattr(ad_doc, "category", None)),
        "currency": _norm(getattr(ad_doc, "currency", None)),
        "price_type": _norm(getattr(ad_doc, "price_type", None)),
        "price": base_price,
        "price_unit": _norm(getattr(ad_doc, "price_unit", None)),
        "current_price": current_price,
        "offer_price": offer_price,
        "offer_percent": _to_float(getattr(ad_doc, "offer_percent", None)),
        "is_offer_active": bool(getattr(ad_doc, "is_offer_active", False)),
        "price_display": _money_display(
            getattr(ad_doc, "currency", None),
            current_price,
        ),
        "primary_image": _primary_image(images),
        "images_count": len(images),
        "created_at": getattr(ad_doc, "creation", None),
        "is_wishlisted": bool(is_wishlisted),
        "average_rating": _to_float(getattr(ad_doc, "average_rating", None)),
        "total_reviews": _to_int(getattr(ad_doc, "total_reviews", 0)),
    }

# Detail View

def serialize_ad_detail(ad_doc, is_wishlisted: bool = False) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)

    # Fetch category_name safely
    category_name = frappe.db.get_value(
        "AOS Category",
        ad_doc.category,
        "category_name",
    ) if ad_doc.category else None

    # Fetch location_name safely
    location_name = frappe.db.get_value(
        "AOS Location",
        ad_doc.location,
        "location_name",
    ) if ad_doc.location else None

    return {
        **serialize_ad_list_item(ad_doc, is_wishlisted=is_wishlisted),
        "description": getattr(ad_doc, "description", None) or "",
        "video": _norm(getattr(ad_doc, "video", None)),
        "images": images,
        "details": serialize_ad_details(ad_doc),
        "category_name": _norm(category_name),
        "location_name": _norm(location_name),
        "seller": _norm(getattr(ad_doc, "user", None)),
    }
