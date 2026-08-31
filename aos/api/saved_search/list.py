from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import (
    LIST_SAVED_SEARCHES_DEFAULT_LIMIT,
    LIST_SAVED_SEARCHES_MAX_LIMIT,
    LIST_SEARCH_LIMIT_PER_MINUTE_PER_USER,
)

_DT = "AOS Saved Search"


def _bounded_non_negative_int(value, *, default: int, maximum: int):
    if value in (None, ""):
        return default, None
    if isinstance(value, bool):
        return None, fail("Pagination value is invalid.", error="VALIDATION_ERROR")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None, fail("Pagination value is invalid.", error="VALIDATION_ERROR")
    if parsed < 0:
        return None, fail("Pagination value is invalid.", error="VALIDATION_ERROR")
    return min(parsed, maximum), None


def list_saved_searches_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    limited = rate_limit(
        key=f"aos:saved-search:list:{user}",
        ttl_seconds=60,
        limit=LIST_SEARCH_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if limited:
        return limited

    limit, err = _bounded_non_negative_int(
        kwargs.get("limit"),
        default=LIST_SAVED_SEARCHES_DEFAULT_LIMIT,
        maximum=LIST_SAVED_SEARCHES_MAX_LIMIT,
    )
    if err:
        return err
    limit = max(1, int(limit or LIST_SAVED_SEARCHES_DEFAULT_LIMIT))

    offset, err = _bounded_non_negative_int(
        kwargs.get("offset"),
        default=0,
        maximum=1_000_000,
    )
    if err:
        return err

    rows = frappe.get_all(
        _DT,
        filters={"user": user, "is_active": 1},
        fields=["name", "title", "params_json", "use_count", "last_used"],
        order_by="last_used desc, name desc",
        offset=offset,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    return ok(
        "Saved searches fetched.",
        data={
            "items": items,
            "has_more": has_more,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + len(items) if has_more else None,
        },
    )
