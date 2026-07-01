"""Update/edit an Ad (status-aware)."""

from __future__ import annotations

from typing import Any, Dict, List

import frappe
from frappe.utils import getdate

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.integrations.ai.image_search_tasks import enqueue_index_refresh_for_status

from .constants import UPDATE_AD_LIMIT_PER_MINUTE_PER_USER
from .media import (
    attach_ad_media,
    get_media_public_url,
    normalize_media_id,
    validate_ad_media_for_use,
)
from .validators import (
    sanitize_details,
    sanitize_images,
    validate_basic_fields,
)

_MAX_IMAGES = 4

_ACTIVE_EDITABLE_FIELDS = {
    "title",
    "description",
    "price_type",
    "price",
    "price_unit",
    "offer_price",
    "offer_start_date",
    "offer_end_date",
}

_BLOCKED_STATUSES = {"Sold", "Expired", "Deleted"}
_FULL_EDIT_STATUSES = {"Reviewing", "Declined"}


def _clean_str(val: Any) -> str:
    return str(val or "").strip()


def _to_float_or_none(val: Any):
    if val in (None, ""):
        return None
    try:
        return float(val)
    except Exception:
        return "INVALID"


def _to_date_or_none(val: Any):
    if val in (None, ""):
        return None
    try:
        return getdate(val)
    except Exception:
        return "INVALID"


def _apply_active_safe_updates(doc, updates: Dict[str, Any]):
    if "title" in updates:
        title = _clean_str(updates.get("title"))
        if not title:
            return fail("Title cannot be empty.", code="VALIDATION_ERROR")
        doc.title = title

    if "description" in updates:
        desc = _clean_str(updates.get("description"))
        if not desc:
            return fail("Description cannot be empty.", code="VALIDATION_ERROR")
        doc.description = desc

    if "price_type" in updates:
        doc.price_type = _clean_str(updates.get("price_type"))

    if "price_unit" in updates:
        doc.price_unit = _clean_str(updates.get("price_unit"))

    if "price" in updates:
        v = _to_float_or_none(updates.get("price"))
        if v == "INVALID":
            return fail("Invalid price.", code="VALIDATION_ERROR")
        doc.price = v

    if "offer_price" in updates:
        v = _to_float_or_none(updates.get("offer_price"))
        if v == "INVALID":
            return fail("Invalid offer_price.", code="VALIDATION_ERROR")
        doc.offer_price = v

    if "offer_start_date" in updates:
        d = _to_date_or_none(updates.get("offer_start_date"))
        if d == "INVALID":
            return fail("Invalid offer_start_date.", code="VALIDATION_ERROR")
        doc.offer_start_date = d

    if "offer_end_date" in updates:
        d = _to_date_or_none(updates.get("offer_end_date"))
        if d == "INVALID":
            return fail("Invalid offer_end_date.", code="VALIDATION_ERROR")
        doc.offer_end_date = d

    return None


def _replace_child_table(doc, fieldname: str, rows: List[Dict[str, Any]]):
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

    row = frappe.db.get_value(
        "AOS Ad",
        ad_id,
        ["name", "seller", "status"],
        as_dict=True,
    )

    if not row:
        return fail("Ad not found.", code="NOT_FOUND")

    seller_user = frappe.db.get_value(
        "AOS Seller",
        row.seller,
        "user",
    )

    if seller_user != user:
        return fail("You don't have permission to edit this ad.", code="FORBIDDEN")

    status = _clean_str(row.status)

    if status in _BLOCKED_STATUSES:
        return fail(
            "This ad cannot be edited in its current status.",
            code="VALIDATION_ERROR",
        )

    try:
        doc = frappe.get_doc("AOS Ad", ad_id)

        if status == "Active":
            updates: Dict[str, Any] = {
                k: kwargs.get(k)
                for k in _ACTIVE_EDITABLE_FIELDS
                if k in kwargs
            }

            if not updates:
                return fail("No editable fields provided.", code="VALIDATION_ERROR")

            e = _apply_active_safe_updates(doc, updates)
            if e:
                return e

            doc.status = "Active"

            doc.save(ignore_permissions=True)

            return ok(
                "Ad updated.",
                data={"id": doc.name, "status": doc.status},
            )

        if status in _FULL_EDIT_STATUSES:
            title, location, category, description, e = validate_basic_fields(
                kwargs.get("title"),
                kwargs.get("location"),
                kwargs.get("category"),
                kwargs.get("description"),
            )

            if e:
                return e

            details_rows = sanitize_details(kwargs.get("details"), category=category,)
            images_rows = sanitize_images(kwargs.get("images"))

            if len(images_rows) > _MAX_IMAGES:
                return fail(
                    f"Maximum {_MAX_IMAGES} images allowed.",
                    code="VALIDATION_ERROR",
                )

            if not images_rows:
                return fail("At least one image is required.", code="VALIDATION_ERROR")

            video_media_id = normalize_media_id(
                kwargs.get("video_media")
                or kwargs.get("video_media_id")
                or kwargs.get("video")
            )
            video_url = ""

            if video_media_id:
                video_doc, e = validate_ad_media_for_use(
                    media_id=video_media_id,
                    user=user,
                    purpose="ad_video",
                    kind="Video",
                    ad_name=doc.name,
                )
                if e:
                    return e
                video_url = get_media_public_url(video_doc.name)

            primary_count = 0
            seen_media: set[str] = set()

            for index, row_img in enumerate(images_rows, start=1):
                media_id = normalize_media_id(row_img.get("media") or row_img.get("media_id"))

                if not media_id:
                    return fail(f"Image media id is required on row {index}.", code="VALIDATION_ERROR")

                if media_id in seen_media:
                    return fail("Duplicate image selected.", code="VALIDATION_ERROR")

                media_doc, e = validate_ad_media_for_use(
                    media_id=media_id,
                    user=user,
                    purpose="ad_image",
                    kind="Image",
                    ad_name=doc.name,
                )

                if e:
                    return e

                seen_media.add(media_id)
                row_img["media"] = media_doc.name
                row_img["media_id"] = media_doc.name
                row_img["image"] = get_media_public_url(media_doc.name)
                row_img["is_primary"] = int(row_img.get("is_primary") or 0)

                if row_img["is_primary"] == 1:
                    primary_count += 1

            if primary_count != 1:
                return fail("Exactly one primary image is required.", code="VALIDATION_ERROR")

            # Core fields
            doc.title = title
            doc.location = location
            doc.category = category
            doc.description = description

            # Pricing
            doc.price_type = kwargs.get("price_type")
            doc.price = _to_float_or_none(kwargs.get("price"))
            doc.price_unit = kwargs.get("price_unit")

            # Offers
            doc.offer_price = _to_float_or_none(kwargs.get("offer_price"))
            doc.offer_start_date = _to_date_or_none(kwargs.get("offer_start_date"))
            doc.offer_end_date = _to_date_or_none(kwargs.get("offer_end_date"))

            # Video
            if any(k in kwargs for k in ("video", "video_media", "video_media_id")):
                doc.video_media = video_media_id or None
                doc.video = video_url or None

            # Replace child tables
            image_child_rows = [
                {k: v for k, v in row.items() if k != "media_id"}
                for row in images_rows
            ]
            _replace_child_table(doc, "details", details_rows)
            _replace_child_table(doc, "images", image_child_rows)

            doc.status = "Reviewing"
            doc.reviewed_by = None

            doc.save(ignore_permissions=True)

            # Attach MinIO media metadata to this ad.
            for r in images_rows:
                _media_doc, attach_error = attach_ad_media(
                    media_id=r.get("media"),
                    user=user,
                    purpose="ad_image",
                    ad_name=doc.name,
                    attached_field="images",
                )
                if attach_error:
                    frappe.db.rollback()
                    return attach_error

            if video_media_id:
                _media_doc, attach_error = attach_ad_media(
                    media_id=video_media_id,
                    user=user,
                    purpose="ad_video",
                    ad_name=doc.name,
                    attached_field="video_media",
                )
                if attach_error:
                    frappe.db.rollback()
                    return attach_error

            try:
                enqueue_index_refresh_for_status(
                    doc.name,
                    status=doc.status,
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Failed to enqueue image-search refresh for {doc.name}",
                )

            return ok(
                "Ad updated and sent for review.",
                data={"id": doc.name, "status": doc.status},
            )

        return fail(
            "This ad cannot be edited in its current status.",
            code="VALIDATION_ERROR",
        )

    except frappe.DoesNotExistError:
        return fail("Ad not found.", code="NOT_FOUND")

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Update Ad Failed",
        )

        return fail(
            "Failed to update ad.",
            code="INTERNAL_ERROR",
        )
