from __future__ import annotations
from typing import Any, Dict
import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from .constants import SAVE_SEARCH_LIMIT_PER_MINUTE_PER_USER
from .utils import generate_fingerprint

_DT = "AOS Saved Search"


def save_search_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:saved-search:save:{user}",
        ttl_seconds=60,
        limit=SAVE_SEARCH_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests."
    )
    if rl:
        return rl

    title = (kwargs.get("title") or "").strip()
    params = kwargs.get("params_json")

    if not title:
        return fail("Title is required.", code="VALIDATION_ERROR")
    if not isinstance(params, dict):
        return fail("params_json must be an object.", code="VALIDATION_ERROR")

    fingerprint = generate_fingerprint(params)

    existing = frappe.db.get_value(
        _DT,
        {"user": user, "fingerprint": fingerprint, "is_active": 1},
        "name"
    )

    if existing:
        return ok("Search already saved.", data={"id": existing})

    doc = frappe.new_doc(_DT)
    doc.user = user
    doc.title = title
    doc.params_json = params
    doc.fingerprint = fingerprint
    doc.last_used = now_datetime()
    doc.use_count = 1
    doc.is_active = 1

    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return ok("Search saved.", data={"id": doc.name})
