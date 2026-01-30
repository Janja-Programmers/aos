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


def validate_basic_fields(title: Any, category: Any, description: Any):
    title = (str(title or "").strip())
    category = (str(category or "").strip())
    description = (str(description or "").strip())

    if not title:
        return None, None, None, fail("Title is required.", code="VALIDATION_ERROR")
    if not category:
        return None, None, None, fail("Category is required.", code="VALIDATION_ERROR")
    if not description:
        return None, None, None, fail("Description is required.", code="VALIDATION_ERROR")

    # Ensure category exists
    if not frappe.db.exists("AOS Category", category):
        return None, None, None, fail("Category not found.", code="NOT_FOUND")

    # Optional: enforce active category if field exists
    try:
        is_active = frappe.db.get_value("AOS Category", category, "is_active")
        if is_active is not None and int(is_active or 0) != 1:
            return None, None, None, fail("Category is inactive.", code="VALIDATION_ERROR")
    except Exception:
        pass

    return title, category, description, None


def sanitize_details(details: Any) -> List[Dict[str, Any]]:
    items = normalize_list_payload(details)
    out: List[Dict[str, Any]] = []
    for item in items:
        row = {k: v for k, v in item.items() if k in ALLOWED_DETAILS_KEYS}
        if row.get("attribute"):
            out.append(row)
    return out


def sanitize_images(images: Any) -> List[Dict[str, Any]]:
    items = normalize_list_payload(images)
    out: List[Dict[str, Any]] = []
    for item in items:
        row = {k: v for k, v in item.items() if k in ALLOWED_IMAGE_KEYS}
        if row.get("image"):
            # normalize booleans
            try:
                row["is_primary"] = int(row.get("is_primary") or 0)
            except Exception:
                row["is_primary"] = 0
            out.append(row)
    return out


def _get_file_by_url(file_url: str):
    if not file_url:
        return None
    return frappe.db.get_value(
        "File",
        {"file_url": file_url},
        [
            "name",
            "file_url",
            "file_name",
            "file_size",
            "attached_to_doctype",
            "attached_to_name",
            "is_private",
        ],
        as_dict=True,
    )


def validate_file_reference(file_url: Any, *, current_user: str, kind: str):
    """Ensure a file_url exists and is safe to attach.

    We allow files that are:
    - currently unattached, or
    - already attached to the same doctype/name we'll set later.

    We reject files attached to other docs (prevents reusing others' uploads).
    """

    file_url = (str(file_url or "").strip())
    if not file_url:
        return "", None

    f = _get_file_by_url(file_url)
    if not f:
        return None, fail(f"{kind} not found. Please upload again.", code="NOT_FOUND")

    # If already attached elsewhere, block
    if f.attached_to_doctype and f.attached_to_name:
        # Allow user re-submitting the same draft? We'll allow only if attached to AOS Ad.
        if f.attached_to_doctype != "AOS Ad":
            return None, fail("You don't have permission to use this file.", code="FORBIDDEN")

    return file_url, None


def attach_file_to_ad(file_url: str, *, ad_name: str):
    """Attach File to AOS Ad doc if not already attached."""
    if not file_url:
        return

    f = _get_file_by_url(file_url)
    if not f:
        return

    if (f.attached_to_doctype or "") == "AOS Ad" and (f.attached_to_name or "") == ad_name:
        return

    # Only attach if not attached, or attached to AOS Ad (but different name)
    # If attached to different AOS Ad, we still re-attach to this one.
    file_doc = frappe.get_doc("File", f.name)
    file_doc.attached_to_doctype = "AOS Ad"
    file_doc.attached_to_name = ad_name
    file_doc.save(ignore_permissions=True)
