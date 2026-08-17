from __future__ import annotations
import json
from typing import Any, Dict
import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail
from .constants import (
    MAX_SAVED_SEARCHES_PER_USER,
    MAX_SAVED_SEARCH_PARAMS_BYTES,
    MAX_SAVED_SEARCH_TITLE_LENGTH,
    SAVE_SEARCH_LIMIT_PER_MINUTE_PER_USER,
)
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
        return fail("Title is required.", error="VALIDATION_ERROR")
    if len(title) > MAX_SAVED_SEARCH_TITLE_LENGTH:
        return fail("Title is too long.", error="VALIDATION_ERROR")
    if not isinstance(params, dict):
        return fail("params_json must be an object.", error="VALIDATION_ERROR")
    try:
        params_bytes = len(json.dumps(params, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    except (TypeError, ValueError):
        return fail("params_json must contain JSON-compatible values.", error="VALIDATION_ERROR")
    if params_bytes > MAX_SAVED_SEARCH_PARAMS_BYTES:
        return fail("params_json is too large.", error="VALIDATION_ERROR")

    fingerprint = generate_fingerprint(params)

    # Saved-search uniqueness is user-scoped. Serializing the check-and-insert
    # boundary on the User row makes duplicate POST retries deterministic across
    # processes without relying on an in-memory mutex.
    frappe.db.sql("SELECT name FROM `tabUser` WHERE name = %s FOR UPDATE", (user,))

    existing = frappe.db.get_value(
        _DT,
        {"user": user, "fingerprint": fingerprint, "is_active": 1},
        "name"
    )

    if existing:
        return ok("Search already saved.", data={"id": existing})

    active_count = frappe.db.count(_DT, {"user": user, "is_active": 1})
    if int(active_count or 0) >= MAX_SAVED_SEARCHES_PER_USER:
        return fail(
            "Saved search limit reached. Delete an existing saved search first.",
            error="SAVED_SEARCH_LIMIT_REACHED",
            data={"max_saved_searches": MAX_SAVED_SEARCHES_PER_USER},
        )

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
