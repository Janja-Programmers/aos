"""Ad Draft endpoints (server-side drafts).

Drafts store the full posting wizard state in JSON.
A draft is converted into a real `AOS Ad` only on submit.

DocType: AOS Ad Draft
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

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

_DT = "AOS Ad Draft"

# Helpers
def _clean_str(val: Any) -> str:
    return str(val or "").strip()


def _parse_jsonish(val: Any) -> Any:
    if val is None:
        return None

    if isinstance(val, (dict, list)):
        return val

    s = _clean_str(val)
    if not s:
        return None

    try:
        import json
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
            import json
            parsed = json.loads(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def _extract_ok_and_id(result: Any) -> Tuple[bool, Optional[str]]:
    if not isinstance(result, dict):
        return False, None

    if isinstance(result.get("message"), dict):
        msg = result.get("message") or {}
        ok_val = bool(msg.get("ok"))
        data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
        return ok_val, data.get("id")

    ok_val = bool(result.get("ok"))
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return ok_val, data.get("id")


def _get_owned_draft(draft_id: str, user: str):
    try:
        doc = frappe.get_doc(_DT, draft_id)
    except Exception:
        return None

    if (doc.user or "") != user:
        return None

    return doc


def _compute_hints(payload: Dict[str, Any]) -> Dict[str, Any]:

    def _get(*keys):
        cur = payload
        for k in keys:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(k)
        return cur

    title = _clean_str(_get("title") or _get("basic", "title"))
    category = _clean_str(_get("category") or _get("basic", "category"))
    country = _clean_str(_get("country") or _get("basic", "country"))
    location = _clean_str(_get("location") or _get("basic", "location"))

    last_step = _get("last_step") or _get("ui", "last_step")

    try:
        last_step = int(last_step)
    except Exception:
        last_step = 1

    return {
        "title_hint": title,
        "category_hint": category or None,
        "country_hint": country or None,
        "location_hint": location or None,
        "last_step": last_step,
    }


# Save / Update Draft
def upsert_ad_draft_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:save:user:{user}",
        ttl_seconds=60,
        limit=SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))
    payload = _parse_jsonish(kwargs.get("payload_json") or kwargs.get("payload"))

    if payload is None:
        return fail("payload_json is required.", code="VALIDATION_ERROR")

    hints = _compute_hints(payload)

    try:
        if draft_id:
            doc = _get_owned_draft(draft_id, user)

            if not doc:
                return fail("Draft not found.", code="NOT_FOUND")

            if doc.status != "Draft":
                return fail("Only Draft items can be updated.", code="VALIDATION_ERROR")

        else:
            doc = frappe.new_doc(_DT)
            doc.status = "Draft"
            doc.user = user

        doc.payload_json = payload

        doc.title_hint = hints["title_hint"]
        doc.category_hint = hints["category_hint"]
        doc.country_hint = hints["country_hint"]
        doc.location_hint = hints["location_hint"]
        doc.last_step = hints["last_step"]

        doc.save(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Draft saved.",
            data={
                "id": doc.name,
                "status": doc.status,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Save Draft Failed")
        return fail("Failed to save draft.", code="INTERNAL_ERROR")


# List Drafts (seller UI)
def list_my_ad_drafts_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:list:user:{user}",
        ttl_seconds=60,
        limit=LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    limit = int(kwargs.get("limit") or 20)
    offset = int(kwargs.get("offset") or 0)

    limit = max(1, min(limit, 50))
    offset = max(0, offset)

    rows = frappe.get_all(
        _DT,
        filters={"user": user, "status": "Draft"},
        fields=[
            "name",
            "title_hint",
            "country_hint",
            "location_hint",
            "modified",
        ],
        order_by="modified desc",
        start=offset,
        page_length=limit,
    )

    items = [
        {
            "id": r.name,
            "title": r.title_hint or "Untitled draft",
            "status": "Draft",
            "country": r.country_hint,
            "location": r.location_hint,
            "current_price": None,
            "primary_image": None,
            "created_at": r.modified,
        }
        for r in rows
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


# Get Draft (for editing)
def get_my_ad_draft_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:get:user:{user}",
        ttl_seconds=60,
        limit=GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))

    if not draft_id:
        return fail("draft_id is required.", code="VALIDATION_ERROR")

    doc = _get_owned_draft(draft_id, user)

    if not doc:
        return fail("Draft not found.", code="NOT_FOUND")

    payload = _as_dict_payload(doc.payload_json) or {}

    payload.update(
        {
            "id": doc.name,
            "status": doc.status,
            "last_step": doc.last_step,
        }
    )

    return ok("Draft fetched.", data={"item": payload})


# Abandon Draft
def abandon_ad_draft_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:abandon:user:{user}",
        ttl_seconds=60,
        limit=ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))

    if not draft_id:
        return fail("draft_id is required.", code="VALIDATION_ERROR")

    doc = _get_owned_draft(draft_id, user)

    if not doc:
        return fail("Draft not found.", code="NOT_FOUND")

    if doc.status != "Draft":
        return fail("Only Draft items can be abandoned.", code="VALIDATION_ERROR")

    try:
        doc.status = "Abandoned"
        doc.save(ignore_permissions=True)

        frappe.db.commit()

        return ok("Draft abandoned.", data={"id": doc.name})

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Abandon Draft Failed")
        return fail("Failed to abandon draft.", code="INTERNAL_ERROR")


# Submit Draft
def submit_ad_draft_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:drafts:submit:user:{user}",
        ttl_seconds=60,
        limit=SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))

    if not draft_id:
        return fail("draft_id is required.", code="VALIDATION_ERROR")

    doc = _get_owned_draft(draft_id, user)

    if not doc:
        return fail("Draft not found.", code="NOT_FOUND")

    if doc.status != "Draft":
        return fail("Only Draft items can be submitted.", code="VALIDATION_ERROR")

    payload = _as_dict_payload(doc.payload_json)

    if not payload:
        return fail("Draft payload invalid.", code="VALIDATION_ERROR")

    result = create_ad_impl(**payload)

    ok_val, ad_id = _extract_ok_and_id(result)

    if not ok_val:
        return result

    try:
        doc.status = "Submitted"
        doc.submitted_ad = ad_id

        doc.save(ignore_permissions=True)

        frappe.db.commit()

        return ok(
            "Draft submitted.",
            data={
                "draft_id": doc.name,
                "submitted_ad": ad_id,
            },
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Submit Draft Failed")

        return fail(
            "Ad created but failed to update draft.",
            code="INTERNAL_ERROR",
            data={"submitted_ad": ad_id},
        )
