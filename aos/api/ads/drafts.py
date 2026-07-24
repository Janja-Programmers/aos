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
from aos.services.ads.api import run_ads_api
from aos.services.ads.constants import DRAFT_ID_FIELDS, DRAFT_LIST_FIELDS
from aos.services.ads.errors import AdsConflictError, AdsNotFoundError
from aos.services.ads.validation import (
    ensure_known_fields,
    normalize_draft_request,
    normalize_identifier,
    normalize_pagination,
)

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
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:drafts:save:user:{user}",
        ttl_seconds=60,
        limit=SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if limited:
        return limited

    def _save():
        draft_id, payload, last_step = normalize_draft_request(kwargs)
        hints = _compute_hints(payload)
        hints["last_step"] = last_step
        if draft_id:
            doc = _get_owned_draft(draft_id, user)
            if not doc:
                raise AdsNotFoundError("Draft not found.")
            if doc.status != "Draft":
                raise AdsConflictError("Only Draft items can be updated.")
        else:
            doc = frappe.new_doc(_DT)
            doc.user = user
            doc.status = "Draft"
        doc.payload_json = payload
        doc.title_hint = hints["title_hint"]
        doc.category_hint = hints["category_hint"]
        doc.country_hint = hints["country_hint"]
        doc.location_hint = hints["location_hint"]
        doc.last_step = hints["last_step"]
        doc.save(ignore_permissions=True)
        return ok("Draft saved.", data={"id": doc.name, "status": doc.status})

    response = run_ads_api(_save, fallback="Failed to save draft.", log_title="AOS Save Draft Failed")
    if not response.get("ok"):
        frappe.db.rollback()
    return response


def list_my_ad_drafts_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:drafts:list:user:{user}",
        ttl_seconds=60,
        limit=LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if limited:
        return limited

    def _list():
        ensure_known_fields(kwargs, DRAFT_LIST_FIELDS)
        limit, offset = normalize_pagination(kwargs)
        default_currency = _clean_str(
            frappe.db.get_value("AOS User Preference", {"user": user}, "currency")
        )
        rows = frappe.get_all(
            _DT,
            filters={"user": user, "status": "Draft"},
            fields=["name", "title_hint", "country_hint", "location_hint", "payload_json", "modified"],
            order_by="modified desc, name desc",
            start=offset,
            page_length=limit,
        )
        items = [_build_draft_list_item(row, default_currency) for row in rows]
        return ok(
            "Drafts fetched.",
            data={"items": items, "pagination": {"limit": limit, "offset": offset, "returned": len(items)}},
        )

    return run_ads_api(_list, fallback="Failed to fetch drafts.", log_title="AOS List Drafts Failed")


def get_my_ad_draft_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:drafts:get:user:{user}",
        ttl_seconds=60,
        limit=GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if limited:
        return limited

    def _get():
        ensure_known_fields(kwargs, DRAFT_ID_FIELDS)
        draft_id = normalize_identifier(kwargs.get("draft_id") or kwargs.get("id"), field="draft_id", required=True)
        doc = _get_owned_draft(draft_id, user)
        if not doc:
            raise AdsNotFoundError("Draft not found.")
        payload = _as_dict_payload(doc.payload_json) or {}
        payload.update({"id": doc.name, "status": doc.status, "last_step": doc.last_step})
        return ok("Draft fetched.", data={"item": payload})

    return run_ads_api(_get, fallback="Failed to fetch draft.", log_title="AOS Get Draft Failed")


def abandon_ad_draft_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:drafts:abandon:user:{user}",
        ttl_seconds=60,
        limit=ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if limited:
        return limited

    def _abandon():
        ensure_known_fields(kwargs, DRAFT_ID_FIELDS)
        draft_id = normalize_identifier(kwargs.get("draft_id") or kwargs.get("id"), field="draft_id", required=True)
        doc = _get_owned_draft(draft_id, user)
        if not doc:
            raise AdsNotFoundError("Draft not found.")
        if doc.status == "Abandoned":
            return ok("Draft abandoned.", data={"id": doc.name, "changed": False})
        if doc.status != "Draft":
            raise AdsConflictError("Only Draft items can be abandoned.")
        doc.status = "Abandoned"
        doc.save(ignore_permissions=True)
        return ok("Draft abandoned.", data={"id": doc.name, "changed": True})

    response = run_ads_api(_abandon, fallback="Failed to abandon draft.", log_title="AOS Abandon Draft Failed")
    if not response.get("ok"):
        frappe.db.rollback()
    return response


def submit_ad_draft_impl(**kwargs):
    user, error = require_login()
    if error:
        return error
    limited = rate_limit(
        key=f"aos:drafts:submit:user:{user}",
        ttl_seconds=60,
        limit=SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if limited:
        return limited

    def _submit():
        ensure_known_fields(kwargs, DRAFT_ID_FIELDS)
        draft_id = normalize_identifier(kwargs.get("draft_id") or kwargs.get("id"), field="draft_id", required=True)
        frappe.db.sql("SELECT name FROM `tabAOS Ad Draft` WHERE name = %s FOR UPDATE", (draft_id,))
        doc = _get_owned_draft(draft_id, user)
        if not doc:
            raise AdsNotFoundError("Draft not found.")
        if doc.status == "Submitted" and doc.submitted_ad:
            return ok(
                "Draft submitted.",
                data={"draft_id": doc.name, "submitted_ad": doc.submitted_ad, "changed": False},
            )
        if doc.status != "Draft":
            raise AdsConflictError("Only Draft items can be submitted.")
        payload = _as_dict_payload(doc.payload_json)
        if not payload:
            raise AdsConflictError("Draft payload is invalid.")
        result = create_ad_impl(**payload)
        success, ad_id = _extract_ok_and_id(result)
        if not success:
            return result
        doc.status = "Submitted"
        doc.submitted_ad = ad_id
        doc.save(ignore_permissions=True)
        return ok(
            "Draft submitted.",
            data={"draft_id": doc.name, "submitted_ad": ad_id, "changed": True},
        )

    response = run_ads_api(_submit, fallback="Failed to submit draft.", log_title="AOS Submit Draft Failed")
    if not response.get("ok"):
        frappe.db.rollback()
    return response
