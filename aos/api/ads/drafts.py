"""Ad Draft endpoints (server-side drafts).

Drafts are intentionally *non-strict* and store the full posting wizard state
in a JSON field. A draft is converted into a real `AOS Ad` only on submit.

DocType: AOS Ad Draft
Key fields used by API:
 - payload_json (JSON, required)
 - status (Draft / Submitted / Abandoned)
 - title_hint, category_hint, country_hint, location_hint, last_step (read-only hints)
 - submitted_ad (Link to AOS Ad when submitted)

All endpoints enforce ownership via `user == current_user`.
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


def _clean_str(val: Any) -> str:
    return str(val or "").strip()


def _parse_jsonish(val: Any) -> Any:
    """Accept dict/list directly, or parse JSON string."""
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
    """Normalize payload_json into a plain dict for create_ad_impl(**payload).

    - If payload is dict/frappe._dict -> return dict(payload)
    - If payload is JSON string -> json.loads
    - Else -> None
    """
    if payload is None:
        return None

    if isinstance(payload, dict):
        return dict(payload)

    if isinstance(payload, str):
        s = payload.strip()
        if not s:
            return None
        try:
            import json

            parsed = json.loads(s)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None

    return None


def _extract_ok_and_id(result: Any) -> Tuple[bool, Optional[str]]:
    """Extract (ok, id) from either response shape.

    Supports:
    1) Flat:    {"ok": True, "message": "...", "data": {"id": "..."}}
    2) Wrapped: {"message": {"ok": True, "message": "...", "data": {"id": "..."}}}
    """
    if not isinstance(result, dict):
        return False, None

    # Wrapped style
    if isinstance(result.get("message"), dict):
        msg = result.get("message") or {}
        ok_val = bool(msg.get("ok"))
        data = msg.get("data") if isinstance(msg.get("data"), dict) else {}
        ad_id = data.get("id")
        return ok_val, ad_id

    # Flat style
    ok_val = bool(result.get("ok"))
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    ad_id = data.get("id")
    return ok_val, ad_id


def _compute_hints(payload: Any) -> Dict[str, Any]:
    """Best-effort extraction of hint fields from payload.

    We support both a flat payload (recommended) and nested payloads where
    values might live under keys like `basic`, `pricing`, etc.
    """

    def _get(*keys: str) -> Any:
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

    last_step = _get("last_step")
    if last_step is None:
        last_step = _get("ui", "last_step")
    try:
        last_step_int = int(last_step) if last_step not in (None, "") else 1
    except Exception:
        last_step_int = 1

    return {
        "title_hint": title or "",
        "category_hint": category or "",
        "country_hint": country or "",
        "location_hint": location or "",
        "last_step": last_step_int,
    }


def _get_owned_draft(draft_id: str, user: str) -> Optional[frappe.model.document.Document]:
    try:
        doc = frappe.get_doc(_DT, draft_id)
    except Exception:
        return None
    if (doc.user or "") != user:
        return None
    return doc


def save_ad_draft_impl(**kwargs):
    """Create or update an ad draft.

    Inputs:
      - draft_id (optional): if present updates existing draft
      - payload_json (required): full draft state (dict/list or JSON string)
    """
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:draft:save:user:{user}",
        ttl_seconds=60,
        limit=SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
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
            if _clean_str(getattr(doc, "status", "")) != "Draft":
                return fail("Only Draft items can be updated.", code="VALIDATION_ERROR")
        else:
            doc = frappe.new_doc(_DT)
            doc.status = "Draft"

        doc.payload_json = payload

        # Hints (read-only in UI, but settable by server)
        doc.title_hint = hints.get("title_hint")
        doc.category_hint = hints.get("category_hint") or None
        doc.country_hint = hints.get("country_hint") or None
        doc.location_hint = hints.get("location_hint") or None
        doc.last_step = hints.get("last_step") or 1

        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok("Draft saved.", data={"id": doc.name, "status": doc.status})

    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Save Ad Draft Failed")
        return fail("Failed to save draft.", code="INTERNAL_ERROR")


def get_ad_draft_impl(**kwargs):
    """Get a single draft by id."""
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:draft:get:user:{user}",
        ttl_seconds=60,
        limit=GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))
    if not draft_id:
        return fail("draft_id is required.", code="VALIDATION_ERROR")

    doc = _get_owned_draft(draft_id, user)
    if not doc:
        return fail("Draft not found.", code="NOT_FOUND")

    return ok(
        "Draft fetched.",
        data={
            "id": doc.name,
            "status": getattr(doc, "status", ""),
            "title_hint": getattr(doc, "title_hint", ""),
            "category_hint": getattr(doc, "category_hint", None),
            "country_hint": getattr(doc, "country_hint", None),
            "location_hint": getattr(doc, "location_hint", None),
            "last_step": getattr(doc, "last_step", 1),
            "payload_json": getattr(doc, "payload_json", None),
            "submitted_ad": getattr(doc, "submitted_ad", None),
            "modified": getattr(doc, "modified", None),
        },
    )


def list_ad_drafts_impl(**kwargs):
    """List current user's drafts.

    Query params:
      - status (optional): default Draft
      - limit (optional): default 20
      - offset (optional): default 0
    """
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:draft:list:user:{user}",
        ttl_seconds=60,
        limit=LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    status = _clean_str(kwargs.get("status") or "Draft") or "Draft"

    limit = 20
    offset = 0
    try:
        if kwargs.get("limit") not in (None, ""):
            limit = max(1, min(int(kwargs.get("limit")), 50))
        if kwargs.get("offset") not in (None, ""):
            offset = max(0, int(kwargs.get("offset")))
    except Exception:
        pass

    filters: Dict[str, Any] = {"user": user}
    if status:
        filters["status"] = status

    try:
        rows = frappe.get_all(
            _DT,
            filters=filters,
            fields=[
                "name",
                "status",
                "title_hint",
                "category_hint",
                "country_hint",
                "location_hint",
                "last_step",
                "submitted_ad",
                "modified",
            ],
            order_by="modified desc",
            start=offset,
            page_length=limit,
        )

        items = [
            {
                "id": r.get("name"),
                "status": r.get("status"),
                "title_hint": r.get("title_hint") or "Untitled draft",
                "category_hint": r.get("category_hint"),
                "country_hint": r.get("country_hint"),
                "location_hint": r.get("location_hint"),
                "last_step": r.get("last_step") or 1,
                "submitted_ad": r.get("submitted_ad"),
                "modified": r.get("modified"),
            }
            for r in rows
        ]

        return ok(
            "Drafts fetched.",
            data={
                "items": items,
                "pagination": {"limit": limit, "offset": offset, "returned": len(items)},
            },
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS List Ad Drafts Failed")
        return fail("Failed to fetch drafts.", code="INTERNAL_ERROR")


def abandon_ad_draft_impl(**kwargs):
    """Mark a draft as Abandoned (soft delete)."""
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:draft:abandon:user:{user}",
        ttl_seconds=60,
        limit=ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))
    if not draft_id:
        return fail("draft_id is required.", code="VALIDATION_ERROR")

    doc = _get_owned_draft(draft_id, user)
    if not doc:
        return fail("Draft not found.", code="NOT_FOUND")

    if _clean_str(getattr(doc, "status", "")) != "Draft":
        return fail("Only Draft items can be abandoned.", code="VALIDATION_ERROR")

    try:
        doc.status = "Abandoned"
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return ok("Draft abandoned.", data={"id": doc.name, "status": doc.status})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Abandon Ad Draft Failed")
        return fail("Failed to abandon draft.", code="INTERNAL_ERROR")


def submit_ad_draft_impl(**kwargs):
    """Submit a draft: create the real Ad and mark draft as Submitted."""
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:draft:submit:user:{user}",
        ttl_seconds=60,
        limit=SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    draft_id = _clean_str(kwargs.get("draft_id") or kwargs.get("id"))
    if not draft_id:
        return fail("draft_id is required.", code="VALIDATION_ERROR")

    doc = _get_owned_draft(draft_id, user)
    if not doc:
        return fail("Draft not found.", code="NOT_FOUND")

    if _clean_str(getattr(doc, "status", "")) != "Draft":
        return fail("Only Draft items can be submitted.", code="VALIDATION_ERROR")

    raw_payload = getattr(doc, "payload_json", None)
    payload = _as_dict_payload(raw_payload)
    if not payload:
        return fail("Draft payload is missing or invalid.", code="VALIDATION_ERROR")

    # Submit by reusing the existing create_ad implementation.
    result = create_ad_impl(**payload)

    ok_val, ad_id = _extract_ok_and_id(result)

    # If create failed, bubble its response directly
    if not ok_val:
        return result

    if not ad_id:
        return fail("Ad created but id missing in response.", code="INTERNAL_ERROR")

    try:
        doc.status = "Submitted"
        doc.submitted_ad = ad_id
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return ok("Draft submitted.", data={"draft_id": doc.name, "submitted_ad": ad_id})
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Submit Ad Draft Failed")
        return fail(
            "Ad created but failed to update draft status.",
            code="INTERNAL_ERROR",
            data={"submitted_ad": ad_id},
        )
