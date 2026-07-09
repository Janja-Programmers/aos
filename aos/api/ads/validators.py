"""Validators/helpers for Ads endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import json

import frappe

from aos.api.shared.responses import fail


ALLOWED_DETAILS_KEYS = {
    "attribute",
    "value_text",
    "value_number",
    "value_date",
    "value_bool",
    "value_json",
}

ALLOWED_IMAGE_KEYS = {
    "image",
    "media",
    "media_id",
    "id",
    "is_primary",
    "sort_order",
}


def _coerce_json(val: Any):
    """Parse JSON when val is a string; otherwise return val."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None
    return None


def normalize_list_payload(val: Any) -> List[Dict[str, Any]]:
    """Accept list payload as list or JSON string."""
    parsed = _coerce_json(val)
    if isinstance(parsed, list):
        out: List[Dict[str, Any]] = []
        for item in parsed:
            if isinstance(item, dict):
                out.append(item)
        return out
    # already list
    if isinstance(val, list):
        return [x for x in val if isinstance(x, dict)]
    return []


def _build_attribute_key_map(category: str) -> Dict[str, str]:
    """Build key -> DocType name map (fuel_type -> Fuel Type)."""

    try:
        from aos.api.catalog.schema import _get_category_chain, _resolve_attributes

        chain = _get_category_chain(category)
        attrs = _resolve_attributes(chain)

        return {
            attr["key"]: attr["id"]
            for attr in attrs
        }
    except Exception:
        return {}


def validate_basic_fields(title: Any, location: Any, category: Any, description: Any):
    title = (str(title or "").strip())
    location = (str(location or "").strip())
    category = (str(category or "").strip())
    description = (str(description or "").strip())

    if not title:
        return None, None, None, None, fail("Title is required.", error="VALIDATION_ERROR")
    if not location:
        return None, None, None, None, fail("Location is required.", error="VALIDATION_ERROR")
    if not category:
        return None, None, None, None, fail("Category is required.", error="VALIDATION_ERROR")
    if not description:
        return None, None, None, None, fail("Description is required.", error="VALIDATION_ERROR")

    # Ensure category exists
    if not frappe.db.exists("AOS Category", category):
        return None, None, None, None, fail("Category not found.", error="NOT_FOUND")

    # Optional: enforce active category if field exists
    try:
        is_active = frappe.db.get_value("AOS Category", category, "is_active")
        if is_active is not None and int(is_active or 0) != 1:
            return None, None, None, None, fail("Category is inactive.", error="VALIDATION_ERROR")
    except Exception:
        pass

    return title, location, category, description, None


def sanitize_details(details: Any, category: str | None = None) -> List[Dict[str, Any]]:
    items = normalize_list_payload(details)
    out: List[Dict[str, Any]] = []

    key_map: Dict[str, str] = {}
    reverse_map: Dict[str, str] = {}

    if category:
        key_map = _build_attribute_key_map(category)
        reverse_map = {v: v for v in key_map.values()}  # allow "Fuel Type"

    for item in items:
        row = {k: v for k, v in item.items() if k in ALLOWED_DETAILS_KEYS}

        attr = (row.get("attribute") or "").strip()

        if not attr:
            continue

        # KEY → ID conversion
        if key_map and attr in key_map:
            row["attribute"] = key_map[attr]

        # Allow already-correct values (Fuel Type)
        elif reverse_map and attr in reverse_map:
            row["attribute"] = attr

        # Invalid attribute
        elif key_map:
            frappe.throw(f"Invalid attribute: {attr}")

        out.append(row)
    return out


def sanitize_images(images: Any) -> List[Dict[str, Any]]:
    """Normalize ad image payloads.

    Clients must send `media` or `media_id` values created by
    `aos.api.media.init_upload` + `confirm_upload`. URL strings are ignored.
    """

    items = normalize_list_payload(images)
    out: List[Dict[str, Any]] = []
    for item in items:
        row = {k: v for k, v in item.items() if k in ALLOWED_IMAGE_KEYS}

        media_id = (
            row.get("media")
            or row.get("media_id")
            or row.get("id")
        )
        if isinstance(media_id, dict):
            media_id = media_id.get("media_id") or media_id.get("id") or media_id.get("name")

        media_id = str(media_id or "").strip()
        if not media_id:
            continue

        clean = {
            "media": media_id,
            "media_id": media_id,
            "image": "",
        }

        try:
            clean["is_primary"] = int(row.get("is_primary") or 0)
        except Exception:
            clean["is_primary"] = 0

        if row.get("sort_order") not in (None, ""):
            try:
                clean["sort_order"] = int(row.get("sort_order"))
            except Exception:
                clean["sort_order"] = None
        else:
            clean["sort_order"] = None

        out.append(clean)
    return out
