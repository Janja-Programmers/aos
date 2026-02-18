"""
Ads serializers.

The mobile app needs two shapes:
1) List item (lightweight)
2) Detail (full fields + children)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import frappe


def _norm(val: Any) -> str:
    return str(val or "").strip()


def _to_float(val: Any) -> Optional[float]:
    if val in (None, ""):
        return None
    try:
        return float(val)
    except Exception:
        return None


def _money_display(currency: str, price_type: str, price: Any) -> str:
    """
    Human display for CURRENT price.
    """

    pt = _norm(price_type)
    cur = _norm(currency)
    amount = _to_float(price)

    if pt == "Contact for price":
        return "Contact for price"

    if pt == "Free":
        return "Free"

    if amount is None:
        return ""

    if cur:
        return f"{cur} {amount:g}"
    return f"{amount:g}"


def _primary_image(images: List[Dict[str, Any]]) -> str:
    for it in images:
        if int(it.get("is_primary") or 0) == 1 and _norm(it.get("image")):
            return _norm(it.get("image"))
    for it in images:
        if _norm(it.get("image")):
            return _norm(it.get("image"))
    return ""


def serialize_ad_images(ad_doc) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in (getattr(ad_doc, "images", []) or []):
        items.append(
            {
                "image": _norm(getattr(row, "image", None)),
                "is_primary": int(getattr(row, "is_primary", 0) or 0),
                "sort_order": int(getattr(row, "sort_order", 0) or 0),
            }
        )

    items.sort(
        key=lambda x: (0 if x["is_primary"] else 1, x["sort_order"], x["image"])
    )
    return items


def serialize_ad_details(ad_doc) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for row in (getattr(ad_doc, "details", []) or []):
        items.append(
            {
                "attribute": _norm(getattr(row, "attribute", None)),
                "value_text": getattr(row, "value_text", None),
                "value_number": getattr(row, "value_number", None),
                "value_date": getattr(row, "value_date", None),
                "value_bool": int(getattr(row, "value_bool", 0) or 0),
                "value_json": getattr(row, "value_json", None),
            }
        )
    return items


def serialize_ad_list_item(ad_doc, is_wishlisted: bool = False) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)
    base_price = getattr(ad_doc, "price", None)
    current_price = getattr(ad_doc, "current_price", base_price)
    offer_price = getattr(ad_doc, "offer_price", None)
    offer_percent = _to_float(getattr(ad_doc, "offer_percent", None))
    is_offer_active = bool(getattr(ad_doc, "is_offer_active", False))

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
        "offer_percent": offer_percent,
        "is_offer_active": is_offer_active,
        "price_display": _money_display(
            getattr(ad_doc, "currency", None),
            getattr(ad_doc, "price_type", None),
            current_price,
        ),
        "primary_image": _primary_image(images),
        "images_count": len(images),
        "created_at": getattr(ad_doc, "creation", None),
        "is_wishlisted": bool(is_wishlisted),
        "average_rating": _to_float(getattr(ad_doc, "average_rating", None)),
        "total_reviews": int(getattr(ad_doc, "total_reviews", 0) or 0),
    }


def serialize_ad_detail(ad_doc, is_wishlisted: bool = False) -> Dict[str, Any]:
    images = serialize_ad_images(ad_doc)

    category_name = None
    try:
        category_name = frappe.db.get_value(
            "AOS Category", ad_doc.category, "category_name"
        )
    except Exception:
        category_name = None

    location_name = None
    try:
        location_name = frappe.db.get_value(
            "AOS Location", ad_doc.location, "location_name"
        )
    except Exception:
        try:
            location_name = frappe.db.get_value(
                "AOS Location", ad_doc.location, "location"
            )
        except Exception:
            location_name = None

    return {
        **serialize_ad_list_item(ad_doc, is_wishlisted=is_wishlisted),
        "description": getattr(ad_doc, "description", None) or "",
        "video": _norm(getattr(ad_doc, "video", None)),
        "images": images,
        "details": serialize_ad_details(ad_doc),
        "category_name": _norm(category_name) if category_name else "",
        "location_name": _norm(location_name) if location_name else "",
        "seller": _norm(getattr(ad_doc, "user", None)),
    }
