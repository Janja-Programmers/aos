"""Validators/helpers for Ads endpoints."""

from __future__ import annotations

from typing import Any, Dict, List

import json

import frappe

from aos.api.shared.responses import fail
from aos.services.catalog.errors import CatalogError, CatalogValidationError, public_catalog_message
from aos.services.catalog.service import CatalogService, resolve_attributes
from aos.services.catalog.validation import normalize_text


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


def _build_attribute_key_map(category: str) -> tuple[Dict[str, str], set[str]]:
    """Build accepted public key and DocType-name sets for one sellable category."""

    service = CatalogService()
    chain = service.get_sellable_category_chain(category)
    attributes = resolve_attributes(chain)
    return (
        {attribute["key"]: attribute["id"] for attribute in attributes},
        {attribute["id"] for attribute in attributes},
    )


def validate_basic_fields(title: Any, location: Any, category: Any, description: Any):
    title = str(title or "").strip()
    location = str(location or "").strip()
    description = str(description or "").strip()

    if not title:
        return None, None, None, None, fail("Title is required.", error="VALIDATION_ERROR")
    if not location:
        return None, None, None, None, fail("Location is required.", error="VALIDATION_ERROR")
    if not description:
        return None, None, None, None, fail("Description is required.", error="VALIDATION_ERROR")

    try:
        category_id = CatalogService().assert_sellable_category(category)
    except CatalogError as exc:
        message = public_catalog_message(exc)
        return None, None, None, None, fail(
            message,
            error=exc.code,
            http_status=exc.http_status,
        )

    return title, location, category_id, description, None


def sanitize_details(details: Any, category: str | None = None) -> List[Dict[str, Any]]:
    items = normalize_list_payload(details)
    out: List[Dict[str, Any]] = []

    key_map: Dict[str, str] = {}
    allowed_ids: set[str] = set()
    schema_loaded = False

    if category:
        key_map, allowed_ids = _build_attribute_key_map(category)
        schema_loaded = True

    for item in items:
        row = {k: v for k, v in item.items() if k in ALLOWED_DETAILS_KEYS}

        try:
            attr = normalize_text(
                row.get("attribute"),
                field="attribute",
                max_length=120,
                required=True,
            )
        except CatalogValidationError:
            frappe.throw("Invalid category attribute.")

        if attr in key_map:
            row["attribute"] = key_map[attr]
        elif attr in allowed_ids:
            row["attribute"] = attr
        elif schema_loaded:
            frappe.throw("Invalid category attribute.")

        out.append(row)
    return out


def sanitize_images(images: Any) -> List[Dict[str, Any]]:
    """Normalize ad image payloads.

    Clients must send `media` or `media_id` values created by
    `aos.api.v1.media.init_upload` + `confirm_upload`. URL strings are ignored.
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
