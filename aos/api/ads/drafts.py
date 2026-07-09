"""Ad Draft endpoints (server-side drafts).

Drafts store the full posting wizard state in JSON.
A draft is converted into a real `AOS Ad` only on submit.

DocType: AOS Ad Draft
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import (
    ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
    GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
    LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER,
    SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
    SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
)
from .create import create_ad_impl
from .serializers import _money_display


_DT = "AOS Ad Draft"


# Helpers
def _clean_str(val: Any) -> str:
    return str(val or "").strip()


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except Exception:
        return default


def _safe_float(val: Any) -> Optional[float]:
    if val in (None, ""):
        return None

    try:
        return float(val)
    except Exception:
        return None


def _is_truthy(val: Any) -> bool:
    if isinstance(val, bool):
        return val

    if isinstance(val, (int, float)):
        return val != 0

    return _clean_str(val).lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }


def _parse_jsonish(val: Any) -> Any:
    if val is None:
        return None

    if isinstance(val, (dict, list)):
        return val

    s = _clean_str(val)
    if not s:
        return None

    try:
        return json.loads(s)
    except Exception:
        return None


def _as_dict_payload(payload: Any) -> Optional[Dict[str, Any]]:
    if payload is None:
        return None

    if isinstance(payload, dict):
        return dict(payload)

    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)

            if isinstance(parsed, dict):
                return parsed

        except Exception:
            return None

    return None


def _extract_ok_and_id(
    result: Any,
) -> Tuple[bool, Optional[str]]:
    if not isinstance(result, dict):
        return False, None

    if isinstance(result.get("message"), dict):
        msg = result.get("message") or {}
        ok_val = bool(msg.get("ok"))

        data = (
            msg.get("data")
            if isinstance(msg.get("data"), dict)
            else {}
        )

        return ok_val, data.get("id")

    ok_val = bool(result.get("ok"))

    data = (
        result.get("data")
        if isinstance(result.get("data"), dict)
        else {}
    )

    return ok_val, data.get("id")


def _get_owned_draft(
    draft_id: str,
    user: str,
):
    try:
        doc = frappe.get_doc(
            _DT,
            draft_id,
        )

    except Exception:
        return None

    if (doc.user or "") != user:
        return None

    return doc


def _get_nested(
    payload: Dict[str, Any],
    *keys: str,
) -> Any:
    current: Any = payload

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def _has_payload_value(value: Any) -> bool:
    if value is None or value == "":
        return False

    if isinstance(
        value,
        (
            list,
            dict,
            tuple,
            set,
        ),
    ):
        return bool(value)

    return True


def _first_payload_value(
    payload: Dict[str, Any],
    *paths: Tuple[str, ...],
) -> Any:
    for path in paths:
        value = _get_nested(
            payload,
            *path,
        )

        if _has_payload_value(value):
            return value

    return None


def _compute_hints(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    title = _clean_str(
        _first_payload_value(
            payload,
            ("title",),
            ("basic", "title"),
        )
    )

    category = _clean_str(
        _first_payload_value(
            payload,
            ("category",),
            ("basic", "category"),
        )
    )

    country = _clean_str(
        _first_payload_value(
            payload,
            ("country",),
            ("basic", "country"),
            ("market", "country"),
        )
    )

    location = _clean_str(
        _first_payload_value(
            payload,
            ("location",),
            ("basic", "location"),
            ("market", "location"),
        )
    )

    last_step = _first_payload_value(
        payload,
        ("last_step",),
        ("ui", "last_step"),
    )

    last_step = max(
        1,
        _safe_int(
            last_step,
            1,
        ),
    )

    return {
        "title_hint": title,
        "category_hint": category or None,
        "country_hint": country or None,
        "location_hint": location or None,
        "last_step": last_step,
    }


def _extract_currency(
    payload: Dict[str, Any],
    default_currency: str,
) -> str:
    currency = _clean_str(
        _first_payload_value(
            payload,
            ("currency",),
            ("pricing", "currency"),
            ("market", "currency"),
            ("basic", "currency"),
        )
    )

    return (
        currency
        or _clean_str(default_currency)
    )


def _extract_price(
    payload: Dict[str, Any],
) -> Optional[float]:
    return _safe_float(
        _first_payload_value(
            payload,
            ("price",),
            ("pricing", "price"),
        )
    )


def _extract_price_type(
    payload: Dict[str, Any],
) -> str:
    return _clean_str(
        _first_payload_value(
            payload,
            ("price_type",),
            ("pricing", "price_type"),
        )
    )


def _normalize_image_rows(
    value: Any,
) -> List[Any]:
    parsed = _parse_jsonish(value)

    if parsed is None:
        if (
            isinstance(value, str)
            and _clean_str(value)
        ):
            return [_clean_str(value)]

        return []

    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        # A single image object.
        if any(
            key in parsed
            for key in (
                "image",
                "file_url",
                "url",
                "path",
            )
        ):
            return [parsed]

        # A wrapper object containing an image list.
        for key in (
            "items",
            "images",
            "files",
        ):
            nested = parsed.get(key)

            if isinstance(nested, list):
                return nested

        # A map of image IDs to image objects.
        return list(parsed.values())

    if (
        isinstance(parsed, str)
        and _clean_str(parsed)
    ):
        return [_clean_str(parsed)]

    return []


def _extract_image_url(
    row: Any,
) -> str:
    if isinstance(row, str):
        return _clean_str(row)

    if not isinstance(row, dict):
        return ""

    return _clean_str(
        row.get("image")
        or row.get("file_url")
        or row.get("url")
        or row.get("path")
    )


def _extract_primary_image(
    payload: Dict[str, Any],
) -> Optional[str]:
    images_value = _first_payload_value(
        payload,
        ("images",),
        ("media", "images"),
        ("media_files", "images"),
    )

    rows = _normalize_image_rows(
        images_value
    )

    primary_candidates: List[
        Tuple[int, str]
    ] = []

    fallback_candidates: List[
        Tuple[int, str]
    ] = []

    for index, row in enumerate(rows):
        image_url = _extract_image_url(
            row
        )

        if not image_url:
            continue

        sort_order = index
        is_primary = False

        if isinstance(row, dict):
            sort_order = _safe_int(
                row.get("sort_order"),
                index,
            )

            is_primary = _is_truthy(
                row.get("is_primary")
            )

        candidate = (
            sort_order,
            image_url,
        )

        fallback_candidates.append(
            candidate
        )

        if is_primary:
            primary_candidates.append(
                candidate
            )

    if primary_candidates:
        primary_candidates.sort(
            key=lambda item: item[0]
        )

        return primary_candidates[0][1]

    if fallback_candidates:
        fallback_candidates.sort(
            key=lambda item: item[0]
        )

        return fallback_candidates[0][1]

    direct_primary = _clean_str(
        _first_payload_value(
            payload,
            ("primary_image",),
            ("media", "primary_image"),
        )
    )

    return direct_primary or None


def _build_draft_list_item(
    row: Any,
    default_currency: str,
) -> Dict[str, Any]:
    payload = _as_dict_payload(
        row.payload_json
    ) or {}

    currency = _extract_currency(
        payload,
        default_currency,
    )

    price = _extract_price(payload)

    price_type = _extract_price_type(
        payload
    )

    current_price = None

    if (
        price is not None
        or price_type
        in {
            "Contact for price",
            "Free",
        }
    ):
        current_price = _money_display(
            currency,
            price,
            price_type,
        )

    return {
        "id": row.name,
        "title": (
            row.title_hint
            or "Untitled draft"
        ),
        "status": "Draft",
        "country": row.country_hint,
        "location": row.location_hint,
        "currency": currency or None,
        "price_type": (
            price_type
            or None
        ),
        "current_price": current_price,
        "primary_image": (
            _extract_primary_image(
                payload
            )
        ),
        "created_at": row.modified,
    }


# Save / Update Draft
def upsert_ad_draft_impl(**kwargs):
    user, err = require_login()

    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:save:user:{user}",
        ttl_seconds=60,
        limit=(
            SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many requests.",
    )

    if rl:
        return rl

    draft_id = _clean_str(
        kwargs.get("draft_id")
        or kwargs.get("id")
    )

    payload = _parse_jsonish(
        kwargs.get("payload_json")
        or kwargs.get("payload")
    )

    if payload is None:
        return fail(
            "payload_json is required.",
            error="VALIDATION_ERROR",
        )

    if not isinstance(payload, dict):
        return fail(
            "payload_json must be a JSON object.",
            error="VALIDATION_ERROR",
        )

    hints = _compute_hints(payload)

    try:
        if draft_id:
            doc = _get_owned_draft(
                draft_id,
                user,
            )

            if not doc:
                return fail(
                    "Draft not found.",
                    error="NOT_FOUND",
                )

            if doc.status != "Draft":
                return fail(
                    "Only Draft items can be updated.",
                    error="VALIDATION_ERROR",
                )

        else:
            doc = frappe.new_doc(_DT)
            doc.status = "Draft"
            doc.user = user

        doc.payload_json = payload

        doc.title_hint = (
            hints["title_hint"]
        )

        doc.category_hint = (
            hints["category_hint"]
        )

        doc.country_hint = (
            hints["country_hint"]
        )

        doc.location_hint = (
            hints["location_hint"]
        )

        doc.last_step = hints["last_step"]

        doc.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        return ok(
            "Draft saved.",
            data={
                "id": doc.name,
                "status": doc.status,
            },
        )

    except Exception:
        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Save Draft Failed",
        )

        return fail(
            "Failed to save draft.",
            error="INTERNAL_ERROR",
        )


# List Drafts (seller UI)
def list_my_ad_drafts_impl(**kwargs):
    user, err = require_login()

    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:list:user:{user}",
        ttl_seconds=60,
        limit=(
            LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many requests.",
    )

    if rl:
        return rl

    limit = min(
        max(
            _safe_int(
                kwargs.get("limit"),
                20,
            ),
            1,
        ),
        50,
    )

    offset = max(
        _safe_int(
            kwargs.get("offset"),
            0,
        ),
        0,
    )

    try:
        default_currency = _clean_str(
            frappe.db.get_value(
                "AOS User Preference",
                {"user": user},
                "currency",
            )
        )

        rows = frappe.get_all(
            _DT,
            filters={
                "user": user,
                "status": "Draft",
            },
            fields=[
                "name",
                "title_hint",
                "country_hint",
                "location_hint",
                "payload_json",
                "modified",
            ],
            order_by="modified desc",
            start=offset,
            page_length=limit,
        )

        items = [
            _build_draft_list_item(
                row,
                default_currency,
            )
            for row in rows
        ]

        return ok(
            "Drafts fetched.",
            data={
                "items": items,
                "pagination": {
                    "limit": limit,
                    "offset": offset,
                    "returned": len(items),
                },
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS List Drafts Failed",
        )

        return fail(
            "Failed to fetch drafts.",
            error="INTERNAL_ERROR",
        )


# Get Draft (for editing)
def get_my_ad_draft_impl(**kwargs):
    user, err = require_login()

    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:get:user:{user}",
        ttl_seconds=60,
        limit=(
            GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many requests.",
    )

    if rl:
        return rl

    draft_id = _clean_str(
        kwargs.get("draft_id")
        or kwargs.get("id")
    )

    if not draft_id:
        return fail(
            "draft_id is required.",
            error="VALIDATION_ERROR",
        )

    doc = _get_owned_draft(
        draft_id,
        user,
    )

    if not doc:
        return fail(
            "Draft not found.",
            error="NOT_FOUND",
        )

    payload = _as_dict_payload(
        doc.payload_json
    ) or {}

    payload.update(
        {
            "id": doc.name,
            "status": doc.status,
            "last_step": doc.last_step,
        }
    )

    return ok(
        "Draft fetched.",
        data={"item": payload},
    )


# Abandon Draft
def abandon_ad_draft_impl(**kwargs):
    user, err = require_login()

    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:drafts:abandon:user:{user}"
        ),
        ttl_seconds=60,
        limit=(
            ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many requests.",
    )

    if rl:
        return rl

    draft_id = _clean_str(
        kwargs.get("draft_id")
        or kwargs.get("id")
    )

    if not draft_id:
        return fail(
            "draft_id is required.",
            error="VALIDATION_ERROR",
        )

    doc = _get_owned_draft(
        draft_id,
        user,
    )

    if not doc:
        return fail(
            "Draft not found.",
            error="NOT_FOUND",
        )

    if doc.status != "Draft":
        return fail(
            "Only Draft items can be abandoned.",
            error="VALIDATION_ERROR",
        )

    try:
        doc.status = "Abandoned"

        doc.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        return ok(
            "Draft abandoned.",
            data={"id": doc.name},
        )

    except Exception:
        frappe.db.rollback()

        frappe.log_error(
            frappe.get_traceback(),
            "AOS Abandon Draft Failed",
        )

        return fail(
            "Failed to abandon draft.",
            error="INTERNAL_ERROR",
        )


# Submit Draft
def submit_ad_draft_impl(**kwargs):
    user, err = require_login()

    if err:
        return err

    rl = rate_limit(
        key=(
            f"aos:drafts:submit:user:{user}"
        ),
        ttl_seconds=60,
        limit=(
            SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER
        ),
        message="Too many requests.",
    )

    if rl:
        return rl

    draft_id = _clean_str(
        kwargs.get("draft_id")
        or kwargs.get("id")
    )

    if not draft_id:
        return fail(
            "draft_id is required.",
            error="VALIDATION_ERROR",
        )

    doc = _get_owned_draft(
        draft_id,
        user,
    )

    if not doc:
        return fail(
            "Draft not found.",
            error="NOT_FOUND",
        )

    if doc.status != "Draft":
        return fail(
            "Only Draft items can be submitted.",
            error="VALIDATION_ERROR",
        )

    payload = _as_dict_payload(
        doc.payload_json
    )

    if not payload:
        return fail(
            "Draft payload invalid.",
            error="VALIDATION_ERROR",
        )

    result = create_ad_impl(**payload)

    ok_val, ad_id = _extract_ok_and_id(
        result
    )

    if not ok_val:
        return result

    try:
        doc.status = "Submitted"
        doc.submitted_ad = ad_id

        doc.save(
            ignore_permissions=True
        )

        frappe.db.commit()

        return ok(
            "Draft submitted.",
            data={
                "draft_id": doc.name,
                "submitted_ad": ad_id,
            },
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Submit Draft Failed",
        )

        return fail(
            "Ad created but failed to update draft.",
            error="INTERNAL_ERROR",
            data={
                "submitted_ad": ad_id
            },
        )
