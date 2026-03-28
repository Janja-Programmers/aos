"""
Utilities for Shorts API.

Keep this file focused on:
- pagination helpers
- light parsing helpers
- response shaping helpers

Do NOT place business logic here.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

import frappe
from frappe.utils import cint, flt

from aos.api.shared.auth import current_user


# JSON HELPERS
def parse_json_if_needed(value: Any, default: Any = None) -> Any:
    """
    Parse JSON string if needed; otherwise return value as-is.
    Returns default on parse failure or empty input.
    """
    if value in (None, "", b""):
        return default

    if isinstance(value, (dict, list)):
        return value

    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8")
        except Exception:
            return default

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default

    return value


def dump_json(value: Any, default: str = "[]") -> str:
    """
    Safely dump Python value to JSON string.
    """
    try:
        return json.dumps(value or [])
    except Exception:
        return default


# CURSOR HELPERS
def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None

    try:
        raw = base64.urlsafe_b64decode(cursor.encode("utf-8"))
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, dict):
            return data
        return None
    except Exception:
        return None


def build_time_id_cursor(*, created_on: Any, name: str) -> str:
    return encode_cursor({
        "created_on": _normalize_datetime_string(created_on),
        "name": name,
    })


def parse_time_id_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    data = decode_cursor(cursor) or {}
    return data.get("created_on"), data.get("name")


def build_cursor_where_clause(
    created_field: str,
    name_field: str,
    cursor: str | None,
) -> tuple[str, tuple]:
    created_on, name = parse_time_id_cursor(cursor)
    if not created_on or not name:
        return "", ()

    clause = f"""
        AND (
            {created_field} < %s
            OR ({created_field} = %s AND {name_field} < %s)
        )
    """
    return clause, (created_on, created_on, name)


# SHORT SERIALIZATION HELPERS
def serialize_short_row(row: dict[str, Any], *, viewer_state: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": row.get("name"),
        "caption": row.get("caption") or "",
        "hashtags": parse_json_if_needed(row.get("hashtags"), default=[]),
        "playback_url": row.get("playback_url"),
        "thumbnail_url": row.get("thumbnail_url"),
        "duration_seconds": flt(row.get("duration_seconds") or 0),
        "view_count": cint(row.get("view_count") or 0),
        "like_count": cint(row.get("like_count") or 0),
        "comment_count": cint(row.get("comment_count") or 0),
        "share_count": cint(row.get("share_count") or 0),
        "impression_count": cint(row.get("impression_count") or 0),
        "ranking_score": flt(row.get("ranking_score") or 0),
        "posted_on": row.get("posted_on"),
        "seller": {
            "id": row.get("seller"),
            "shop_name": row.get("shop_name"),
            "avatar": row.get("seller_avatar"),
        },
        "ad": {
            "id": row.get("ad"),
            "title": row.get("ad_title"),
            "price": row.get("ad_price"),
            "currency": row.get("ad_currency"),
            "thumbnail": row.get("ad_thumbnail"),
        },
        "viewer_state": viewer_state or {},
    }


def serialize_comment_row(row: dict[str, Any], *, viewer_state: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": row.get("name"),
        "short": row.get("short"),
        "user": row.get("user"),
        "seller": row.get("seller"),
        "comment": row.get("comment") or "",
        "parent_comment": row.get("parent_comment"),
        "root_comment": row.get("root_comment"),
        "reply_count": cint(row.get("reply_count") or 0),
        "like_count": cint(row.get("like_count") or 0),
        "status": row.get("status"),
        "created_at": row.get("creation"),
        "viewer_state": viewer_state or {},
    }


# USER / SESSION HELPERS
def resolve_actor(
    user: str | None = None,
    session_id: str | None = None,
) -> tuple[str | None, str | None]:
    """
    Returns normalized (user, session_id).
    Logged-in user wins; guest relies on session_id.
    """
    user = user or current_user()

    if user and user != "Guest":
        return user, None

    return None, (session_id or None)


# INTERNAL HELPERS
def _normalize_datetime_string(value: Any) -> str | None:
    if value in (None, ""):
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    return str(value)
