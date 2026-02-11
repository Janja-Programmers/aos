"""Update/edit an Ad (status-aware).

This endpoint supports two edit modes depending on the Ad's current status:

1) Active ads (published):
   - Allow editing a SAFE subset of fields only:
     title, description, price_type, price, price_unit
   - Status remains Active.

2) Reviewing or Declined ads (not published / needs moderation):
   - Allow FULL edits (similar to create/submit):
     title, category, location, description, details, images, video, pricing fields
   - Review is restarted:
     status -> Reviewing
     reviewed_by -> None

Rules:
- User must be logged in and must own the ad (owner).
- Sold/Expired/Deleted cannot be edited here.
"""

from __future__ import annotations

from typing import Any, Dict, List

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import UPDATE_AD_LIMIT_PER_MINUTE_PER_USER
from .validators import (
    attach_file_to_ad,
    sanitize_details,
    sanitize_images,
    validate_basic_fields,
    validate_file_reference,
)

# Safe-only editable fields for Active ads
_ACTIVE_EDITABLE_FIELDS = {
    "title",
    "description",
    "price_type",
    "price",
    "price_unit",
}

# Status buckets
_BLOCKED_STATUSES = {"Sold", "Expired", "Deleted"}
_FULL_EDIT_STATUSES = {"Reviewing", "Declined"}


def _clean_str(val: Any) -> str:
    return str(val or "").strip()


def _to_float_or_none(val: Any):
    if val is None:
        return None
    if isinstance(val, str) and not val.strip():
        return None
    try:
        return float(val)
    except Exception:
        return "INVALID"


def _apply_active_safe_updates(doc, updates: Dict[str, Any]):
    """Apply safe updates for Active ads only. Keeps status Active."""
    # Validate/normalize safe fields
    if "title" in updates:
        title = _clean_str(updates.get("title"))
        if not title:
            return fail("Title cannot be empty.", code="VALIDATION_ERROR")
        doc.title = title

    if "description" in updates:
        doc.description = _clean_str(updates.get("description"))

    if "price_type" in updates:
        doc.price_type = _clean_str(updates.get("price_type")) if updates.get("price_type") is not None else None

    if "price_unit" in updates:
        doc.price_unit = _clean_str(updates.get("price_unit")) if updates.get("price_unit") is not None else None

    if "price" in updates:
        v = _to_float_or_none(updates.get("price"))
        if v == "INVALID":
            return fail("Invalid price.", code="VALIDATION_ERROR")
        doc.price = v

    return None


def _replace_child_table(doc, fieldname: str, rows: List[Dict[str, Any]]):
    """Clear and rebuild a child table."""
    doc.set(fieldname, [])
    for r in rows:
        child = doc.append(fieldname, {})
        for k, v in r.items():
            setattr(child, k, v)


def update_ad_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:update:user:{user}",
        ttl_seconds=60,
        limit=UPDATE_AD_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad_id = _clean_str(kwargs.get("ad_id") or kwargs.get("id"))
    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    # Fetch minimal info first for ownership + status checks.
    row = frappe.db.get_value(
        "AOS Ad",
        ad_id,
        ["name", "owner", "status"],
        as_dict=True,
    )
    if not row:
        return fail("Ad not found.", code="NOT_FOUND")

    if _clean_str(row.owner) != user:
        return fail("You don't have permission to edit this ad.", code="FORBIDDEN")

    status = _clean_str(row.status)
    if status in _BLOCKED_STATUSES:
        return fail("This ad cannot be edited in its current status.", code="VALIDATION_ERROR")

    try:
        doc = frappe.get_doc("AOS Ad", ad_id)

        # MODE A: Active ads -> safe subset only, status remains Active
        if status == "Active":
            updates: Dict[str, Any] = {k: kwargs.get(k) for k in _ACTIVE_EDITABLE_FIELDS if k in kwargs}
            if not updates:
                return fail("No editable fields provided.", code="VALIDATION_ERROR")

            e = _apply_active_safe_updates(doc, updates)
            if e:
                return e

            # Keep Active explicitly (defensive)
            doc.status = "Active"

            doc.save(ignore_permissions=True)
            frappe.db.commit()
            return ok("Ad updated.", data={"id": doc.name, "status": doc.status})

        # MODE B: Reviewing/Declined -> full edit (like create), restart review
        if status in _FULL_EDIT_STATUSES:
            # We expect full payload similar to create.
            # Required: title, location, category (validate_basic_fields enforces this)
            title, location, category, description, e = validate_basic_fields(
                kwargs.get("title"),
                kwargs.get("location"),
                kwargs.get("category"),
                kwargs.get("description"),
            )
            if e:
                return e

            details_rows = sanitize_details(kwargs.get("details"))
            images_rows = sanitize_images(kwargs.get("images"))

            # video is optional
            video_url, e = validate_file_reference(
                kwargs.get("video"),
                current_user=user,
                kind="Video",
            )
            if e:
                return e

            # Validate image file references early (nice API errors). Controller validates count/primary too.
            for row_img in images_rows:
                img_url, e = validate_file_reference(
                    row_img.get("image"),
                    current_user=user,
                    kind="Image",
                )
                if e:
                    return e
                row_img["image"] = img_url

            # Pricing fields are category-driven; accept what client submits.
            price_type = kwargs.get("price_type")
            currency = kwargs.get("currency")
            price = kwargs.get("price")
            price_unit = kwargs.get("price_unit")

            # Apply core fields
            doc.title = title
            doc.location = location
            doc.category = category
            doc.description = description

            # Pricing
            if currency is not None and _clean_str(currency) != "":
                doc.currency = currency
            if price_type is not None:
                doc.price_type = price_type
            if price is not None and _clean_str(price) != "":
                doc.price = price
            else:
                # allow clearing price
                if "price" in kwargs:
                    doc.price = None
            if price_unit is not None:
                doc.price_unit = price_unit

            # Media (video replace/clear)
            if "video" in kwargs:
                doc.video = video_url or None

            # Replace Details child table
            _replace_child_table(doc, "details", details_rows)

            # Replace Images child table (keep only the fields you use)
            doc.set("images", [])
            for r in images_rows:
                child = doc.append("images", {})
                child.image = r.get("image")
                child.is_primary = int(r.get("is_primary") or 0)
                if "sort_order" in r and r.get("sort_order") not in (None, ""):
                    child.sort_order = r.get("sort_order")

            # Restart review
            doc.status = "Reviewing"
            doc.reviewed_by = None

            # Save triggers AOSAd.validate() (details/pricing/media/location)
            doc.save(ignore_permissions=True)

            # Attach uploaded files to the Ad so they aren't orphaned
            for r in images_rows:
                attach_file_to_ad(r.get("image") or "", ad_name=doc.name)
            if video_url:
                attach_file_to_ad(video_url, ad_name=doc.name)

            frappe.db.commit()
            return ok("Ad updated and sent for review.", data={"id": doc.name, "status": doc.status})

        # Fallback (shouldn't happen if statuses are controlled)
        return fail("This ad cannot be edited in its current status.", code="VALIDATION_ERROR")

    except frappe.DoesNotExistError:
        return fail("Ad not found.", code="NOT_FOUND")
    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Update Ad Failed")
        return fail("Failed to update ad.", code="INTERNAL_ERROR")
