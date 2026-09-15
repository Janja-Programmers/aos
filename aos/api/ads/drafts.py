"""Canonical owner Ad Draft endpoints.

Drafts persist only the normalized posting-wizard payload. They have opaque
public IDs, optimistic versions, strict ownership and no legacy payload aliases.
"""
from __future__ import annotations

from typing import Any
import frappe

from aos.services.media.media_service import MediaService
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok
from aos.services.ads.api import run_ads_api
from aos.services.ads.concurrency import normalize_version
from aos.services.ads.constants import DRAFT_ID_FIELDS, DRAFT_LIST_FIELDS
from aos.services.ads.errors import AdsConflictError, AdsNotFoundError
from aos.services.ads.validation import ensure_known_fields, normalize_draft_request, normalize_identifier, normalize_pagination
from aos.services.marketplace_discovery.ids import resolve_ad_name, resolve_public_name

from .constants import (
    ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER, GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
    LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER, SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
    SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,
)
from .create import create_ad_impl
from .serializers import _money_display

_DT="AOS Ad Draft"


def _owned(public_id: str, user: str, *, lock: bool=False):
    name=resolve_public_name(_DT,public_id)
    if not name: raise AdsNotFoundError("Draft not found.")
    if lock: frappe.db.sql("SELECT name FROM `tabAOS Ad Draft` WHERE name=%s FOR UPDATE",(name,))
    doc=frappe.get_doc(_DT,name)
    if str(doc.user or "") != user: raise AdsNotFoundError("Draft not found.")
    return doc


def _assert_version(doc, value):
    expected=normalize_version(value)
    if str(doc.modified or "") != expected:
        raise AdsConflictError("The draft changed since it was loaded.", code="AD_CONFLICT")


def _hints(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "title_hint": str(payload.get("title") or "")[:140] or None,
        "category_hint": str(payload.get("category") or "") or None,
        "location_hint": str(payload.get("location") or "") or None,
    }


def _payload_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict): return dict(value)
    if isinstance(value, str):
        try:
            parsed=frappe.parse_json(value)
            return dict(parsed) if isinstance(parsed,dict) else {}
        except Exception: return {}
    return {}


def _primary_media_id(row) -> str:
    payload=_payload_dict(row.payload_json); images=payload.get("images") or []
    primary=next((item for item in images if isinstance(item,dict) and int(item.get("is_primary") or 0)==1), images[0] if images else None)
    return str(primary.get("media") or "").strip() if isinstance(primary,dict) else ""


def _preview(row, currency: str, media_urls: dict[str,str] | None=None) -> dict[str, Any]:
    payload=_payload_dict(row.payload_json)
    media_id=_primary_media_id(row); image_url=(media_urls or {}).get(media_id) if media_id else None
    price_type=str(payload.get("price_type") or "")
    price=payload.get("price")
    return {
        "id":row.public_id,"version":str(row.modified),"title":row.title_hint or "Untitled draft","status":"Draft",
        "location":row.location_hint,"category":row.category_hint,"currency":currency or None,"price_type":price_type or None,
        "current_price":_money_display(currency,price,price_type) if price is not None or price_type in {"Contact for price","Free"} else None,
        "primary_image":image_url,"modified_at":row.modified,
    }


def upsert_ad_draft_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:drafts:save:user:{user}",ttl_seconds=60,limit=SAVE_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _save():
        public_id,payload,last_step=normalize_draft_request(kwargs); hints=_hints(payload)
        if public_id:
            doc=_owned(public_id,user,lock=True); _assert_version(doc,kwargs.get("version"))
            if doc.status!="Draft": raise AdsConflictError("Only Draft items can be updated.")
        else:
            if kwargs.get("version") not in (None,""): raise AdsConflictError("A new draft cannot have a version.", code="AD_CONFLICT")
            doc=frappe.new_doc(_DT); doc.user=user; doc.status="Draft"
        doc.payload_json=payload; doc.title_hint=hints["title_hint"]; doc.category_hint=hints["category_hint"]; doc.location_hint=hints["location_hint"]; doc.last_step=last_step
        doc.save(ignore_permissions=True)
        return ok("Draft saved.",data={"id":doc.public_id,"status":doc.status,"version":str(doc.modified)})
    return run_ads_api(_save,fallback="Failed to save draft.",log_title="AOS Save Draft Failed")


def list_my_ad_drafts_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:drafts:list:user:{user}",ttl_seconds=60,limit=LIST_AD_DRAFTS_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _list():
        ensure_known_fields(kwargs,DRAFT_LIST_FIELDS); limit,offset=normalize_pagination(kwargs)
        currency=str(frappe.db.get_value("AOS User Preference",{"user":user},"currency") or "")
        rows=frappe.get_all(_DT,filters={"user":user,"status":"Draft"},fields=["name","public_id","title_hint","category_hint","location_hint","payload_json","modified"],order_by="modified desc, public_id desc",offset=offset,limit=limit)
        media_ids=[mid for row in rows if (mid:=_primary_media_id(row))]
        media_urls=MediaService().get_public_url_map(media_ids) if media_ids else {}
        return ok("Drafts fetched.",data={"items":[_preview(row,currency,media_urls) for row in rows],"pagination":{"limit":limit,"offset":offset,"returned":len(rows)}})
    return run_ads_api(_list,fallback="Failed to fetch drafts.",log_title="AOS List Drafts Failed")


def get_my_ad_draft_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:drafts:get:user:{user}",ttl_seconds=60,limit=GET_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _get():
        ensure_known_fields(kwargs,{"draft_id"}); public_id=normalize_identifier(kwargs.get("draft_id"),field="draft_id",required=True); doc=_owned(public_id,user)
        payload=_payload_dict(doc.payload_json)
        return ok("Draft fetched.",data={"item":{"id":doc.public_id,"version":str(doc.modified),"status":doc.status,"last_step":doc.last_step,"payload":payload}})
    return run_ads_api(_get,fallback="Failed to fetch draft.",log_title="AOS Get Draft Failed")


def abandon_ad_draft_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:drafts:abandon:user:{user}",ttl_seconds=60,limit=ABANDON_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _abandon():
        ensure_known_fields(kwargs,DRAFT_ID_FIELDS); public_id=normalize_identifier(kwargs.get("draft_id"),field="draft_id",required=True); doc=_owned(public_id,user,lock=True)
        if doc.status=="Abandoned": return ok("Draft abandoned.",data={"id":doc.public_id,"version":str(doc.modified),"changed":False})
        _assert_version(doc,kwargs.get("version"))
        if doc.status!="Draft": raise AdsConflictError("Only Draft items can be abandoned.")
        doc.status="Abandoned"; doc.save(ignore_permissions=True)
        return ok("Draft abandoned.",data={"id":doc.public_id,"version":str(doc.modified),"changed":True})
    return run_ads_api(_abandon,fallback="Failed to abandon draft.",log_title="AOS Abandon Draft Failed")


def submit_ad_draft_impl(**kwargs):
    user,error=require_login()
    if error:return error
    limited=rate_limit(key=f"aos:drafts:submit:user:{user}",ttl_seconds=60,limit=SUBMIT_AD_DRAFT_LIMIT_PER_MINUTE_PER_USER,message="Too many requests.")
    if limited:return limited
    def _submit():
        ensure_known_fields(kwargs,DRAFT_ID_FIELDS); public_id=normalize_identifier(kwargs.get("draft_id"),field="draft_id",required=True); doc=_owned(public_id,user,lock=True)
        if doc.status=="Submitted" and doc.submitted_ad:
            submitted_public=str(frappe.db.get_value("AOS Ad",doc.submitted_ad,"public_id") or "")
            return ok("Draft submitted.",data={"draft_id":doc.public_id,"submitted_ad_id":submitted_public,"changed":False})
        _assert_version(doc,kwargs.get("version"))
        if doc.status!="Draft": raise AdsConflictError("Only Draft items can be submitted.")
        payload=_payload_dict(doc.payload_json)
        if not payload: raise AdsConflictError("Draft payload is invalid.")
        payload["idempotency_key"]=f"draft-submit:{doc.public_id}"
        result=create_ad_impl(**payload)
        if not isinstance(result,dict) or not result.get("ok"): return result
        public_ad_id=str((result.get("data") or {}).get("id") or ""); internal_ad=resolve_ad_name(public_ad_id)
        doc.status="Submitted"; doc.submitted_ad=internal_ad; doc.save(ignore_permissions=True)
        return ok("Draft submitted.",data={"draft_id":doc.public_id,"submitted_ad_id":public_ad_id,"changed":True})
    return run_ads_api(_submit,fallback="Failed to submit draft.",log_title="AOS Submit Draft Failed")
