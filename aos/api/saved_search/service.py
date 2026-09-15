"""Canonical Saved Search owner service."""
from __future__ import annotations
import json
import frappe

from aos.services.ads.concurrency import normalize_version
from aos.services.ads.errors import AdsConflictError, AdsNotFoundError, AdsValidationError
from aos.services.ads.validation import normalize_identifier, normalize_int, normalize_text
from aos.services.marketplace_discovery.search_query import persisted_search_intent
from .constants import MAX_SAVED_SEARCHES_PER_USER, MAX_SAVED_SEARCH_PARAMS_BYTES, MAX_SAVED_SEARCH_TITLE_LENGTH
from .utils import decode_cursor, encode_cursor, generate_fingerprint

_DT="AOS Saved Search"


def _title(value):
    return normalize_text(value,field="title",max_length=MAX_SAVED_SEARCH_TITLE_LENGTH,required=True)


def _query(value):
    if not isinstance(value,dict): raise AdsValidationError("query must be an object.",code="SEARCH_INVALID_FILTERS")
    result=persisted_search_intent(value)
    size=len(json.dumps(result,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode())
    if size>MAX_SAVED_SEARCH_PARAMS_BYTES: raise AdsValidationError("Saved search is too large.",code="SEARCH_INVALID_FILTERS")
    return result


def _lock_user(user:str):
    frappe.db.sql("SELECT name FROM `tabUser` WHERE name=%s FOR UPDATE",(user,))


def _owned(public_id:str,user:str,*,lock=False):
    filters={"public_id":public_id,"user":user,"is_active":1}
    row=frappe.db.get_value(
        _DT,
        filters,
        ["name","public_id","user","title","params_json","fingerprint","is_active","modified"],
        as_dict=True,
    )
    if not row:
        raise AdsNotFoundError("Saved search not found.",code="SAVED_SEARCH_NOT_FOUND")
    if lock:
        locked=frappe.db.sql(
            "SELECT name FROM `tabAOS Saved Search` WHERE name=%s AND user=%s AND is_active=1 FOR UPDATE",
            (row.name,user),
        )
        if not locked:
            raise AdsNotFoundError("Saved search not found.",code="SAVED_SEARCH_NOT_FOUND")
        row=frappe.db.get_value(
            _DT,
            {"name":row.name,"user":user,"is_active":1},
            ["name","public_id","user","title","params_json","fingerprint","is_active","modified"],
            as_dict=True,
        )
        if not row:
            raise AdsNotFoundError("Saved search not found.",code="SAVED_SEARCH_NOT_FOUND")
    return row


def _assert_version(row,value):
    expected=normalize_version(value)
    if str(row.modified or "")!=expected: raise AdsConflictError("Saved search changed since it was loaded.",code="SAVED_SEARCH_CONFLICT")


def _params(value):
    if isinstance(value,dict): return dict(value)
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            return parsed if isinstance(parsed,dict) else {}
        except Exception: return {}
    return {}


def create(*,user:str,title,query):
    title=_title(title); query=_query(query); fingerprint=generate_fingerprint(query); _lock_user(user)
    existing=frappe.db.get_value(_DT,{"user":user,"fingerprint":fingerprint,"is_active":1},["public_id","modified"],as_dict=True)
    if existing: raise AdsConflictError("Search is already saved.",code="SAVED_SEARCH_CONFLICT")
    if int(frappe.db.count(_DT,{"user":user,"is_active":1}) or 0)>=MAX_SAVED_SEARCHES_PER_USER:
        raise AdsConflictError("Saved search limit reached.",code="SAVED_SEARCH_LIMIT_REACHED")
    doc=frappe.new_doc(_DT); doc.user=user; doc.title=title; doc.params_json=query; doc.fingerprint=fingerprint; doc.is_active=1; doc.insert(ignore_permissions=True)
    return doc


def update(*,user:str,public_id:str,title,query,version):
    _lock_user(user); row=_owned(public_id,user,lock=True); _assert_version(row,version)
    title=_title(title); query=_query(query); fingerprint=generate_fingerprint(query)
    duplicate=frappe.db.sql("SELECT name FROM `tabAOS Saved Search` WHERE user=%s AND fingerprint=%s AND is_active=1 AND name!=%s LIMIT 1",(user,fingerprint,row.name))
    if duplicate: raise AdsConflictError("Search is already saved.",code="SAVED_SEARCH_CONFLICT")
    doc=frappe.get_doc(_DT,row.name); doc.title=title; doc.params_json=query; doc.fingerprint=fingerprint; doc.save(ignore_permissions=True); return doc


def delete(*,user:str,public_id:str,version):
    _lock_user(user); row=_owned(public_id,user,lock=True); _assert_version(row,version)
    doc=frappe.get_doc(_DT,row.name); doc.is_active=0; doc.save(ignore_permissions=True); return doc


def list_owned(*,user:str,limit, cursor):
    limit=normalize_int(limit,field="limit",default=20,minimum=1,maximum=50)
    values={"user":user,"limit":limit+1}; where=["user=%(user)s","is_active=1"]
    if cursor:
        modified,public_id=decode_cursor(cursor); values.update({"cursor_modified":modified,"cursor_public_id":public_id})
        where.append("(modified < %(cursor_modified)s OR (modified=%(cursor_modified)s AND public_id < %(cursor_public_id)s))")
    rows=frappe.db.sql(f"SELECT public_id,title,params_json,modified FROM `tabAOS Saved Search` WHERE {' AND '.join(where)} ORDER BY modified DESC, public_id DESC LIMIT %(limit)s",values,as_dict=True)
    has_more=len(rows)>limit; rows=rows[:limit]
    items=[{"id":r.public_id,"title":r.title,"query":_params(r.params_json),"version":str(r.modified),"modified_at":r.modified} for r in rows]
    next_cursor=encode_cursor(modified=rows[-1].modified,public_id=rows[-1].public_id) if has_more and rows else None
    return items,next_cursor
