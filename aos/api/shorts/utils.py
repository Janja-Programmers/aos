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
import hashlib
import hmac
import json
import time
from datetime import datetime
from typing import Any

import frappe
from frappe.utils import cint, flt

from aos.services.accounts.identity import public_account_id_for_user
from aos.services.sellers.identity import public_seller_id_for_name
from aos.services.shorts.errors import ShortsCursorError

from aos.api.shared.auth import current_user
from aos.api.shared.sql_safety import require_dotted_sql_identifier
from aos.api.shared.user_display import get_user_display
from aos.api.shared.formatters import humanize_count
from aos.api.shorts.constants import (
    DEFAULT_SHORT_CONTENT_MODE,
    DEFAULT_SHORT_AUDIENCE,
    DEFAULT_ALLOW_COMMENTS,
    DEFAULT_ALLOW_DOWNLOADS,
)


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


# VIEWER STATE DEFAULTS
def default_short_viewer_state(
    *,
    target_user: str | None = None,
) -> dict[str, Any]:
    """
    Stable default viewer state for shorts.

    Used for guests or when a caller did not provide preloaded viewer state.

    Relationship meaning:
      - is_following: current viewer follows target_user
      - is_followed_by: target_user follows current viewer
      - is_friend: both users follow each other
    """
    return {
        "is_liked": False,
        "is_saved": False,
        "is_reposted": False,

        # Relationship state
        "target_user": public_account_id_for_user(target_user),
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
        "is_blocked_by_me": False,
        "has_blocked_me": False,
        "is_blocked": False,
        "block_status": "none",
        "can_follow": False,
        "can_message": False,
        "can_call": False,
        "can_view_profile": bool(target_user),

        # Ownership/actions
        "is_owner": False,
        "can_edit": False,
        "can_delete": False,
        "can_report": False,
        "can_repost": False,
        "can_share": False,
    }


def default_comment_viewer_state() -> dict[str, bool]:
    """
    Stable default viewer state for comments.
    """
    return {
        "is_liked": False,
        "is_owner": False,
        "can_delete": False,
        "can_report": False,
        "can_repost": False,
        "can_share": False,
    }


# CURSOR HELPERS
_CURSOR_MAX_LENGTH = 2048
_CURSOR_TTL_SECONDS = 24 * 60 * 60


def _cursor_secret() -> bytes:
    config = {}
    try:
        config.update(dict(getattr(frappe.local, "conf", {}) or {}))
    except Exception:
        pass
    if not config:
        try:
            config.update(dict(frappe.get_site_config() or {}))
        except Exception:
            pass
    value = str(config.get("encryption_key") or config.get("db_password") or "").strip()
    if not value:
        raise ShortsCursorError("Shorts pagination is temporarily unavailable.")
    return value.encode("utf-8")


def encode_cursor(payload: dict[str, Any]) -> str:
    envelope = {
        "v": 1,
        "exp": int(time.time()) + _CURSOR_TTL_SECONDS,
        "d": payload,
    }
    body = json.dumps(envelope, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    signature = hmac.new(_cursor_secret(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + signature).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if cursor in (None, ""):
        return None
    if not isinstance(cursor, str) or len(cursor) > _CURSOR_MAX_LENGTH:
        raise ShortsCursorError()
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if len(raw) <= 32:
            raise ValueError
        body, signature = raw[:-32], raw[-32:]
        expected = hmac.new(_cursor_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        envelope = json.loads(body.decode("utf-8"))
        if envelope.get("v") != 1 or not isinstance(envelope.get("d"), dict):
            raise ValueError
        if int(envelope.get("exp") or 0) < int(time.time()):
            raise ValueError
        return dict(envelope["d"])
    except ShortsCursorError:
        raise
    except Exception:
        raise ShortsCursorError() from None


def build_time_id_cursor(*, created_on: Any, name: str) -> str:
    return encode_cursor(
        {
            "created_on": _normalize_datetime_string(created_on),
            "name": name,
        }
    )


def parse_time_id_cursor(cursor: str | None) -> tuple[str | None, str | None]:
    data = decode_cursor(cursor) or {}
    return data.get("created_on"), data.get("name")


def build_cursor_where_clause(
    created_field: str,
    name_field: str,
    cursor: str | None,
) -> tuple[str, tuple]:
    created_field = require_dotted_sql_identifier(created_field, label="cursor created field")
    name_field = require_dotted_sql_identifier(name_field, label="cursor name field")

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


def build_ranked_cursor(
    *,
    ranking_score: Any,
    created_on: Any,
    name: str,
) -> str:
    """Cursor for ranking_score DESC, creation DESC, name DESC."""
    return encode_cursor(
        {
            "ranking_score": flt(ranking_score or 0),
            "created_on": _normalize_datetime_string(created_on),
            "name": name,
        }
    )


def parse_ranked_cursor(
    cursor: str | None,
) -> tuple[float | None, str | None, str | None]:
    data = decode_cursor(cursor) or {}
    ranking_score = data.get("ranking_score")
    created_on = data.get("created_on")
    name = data.get("name")
    if ranking_score is None or not created_on or not name:
        return None, None, None
    return flt(ranking_score), created_on, name


def build_ranked_cursor_where_clause(
    score_field: str,
    created_field: str,
    name_field: str,
    cursor: str | None,
) -> tuple[str, tuple]:
    score_field = require_dotted_sql_identifier(score_field, label="cursor score field")
    created_field = require_dotted_sql_identifier(created_field, label="cursor created field")
    name_field = require_dotted_sql_identifier(name_field, label="cursor name field")

    ranking_score, created_on, name = parse_ranked_cursor(cursor)
    if ranking_score is None or not created_on or not name:
        return "", ()

    score_expr = f"COALESCE({score_field}, 0)"
    clause = f"""
        AND (
            {score_expr} < %s
            OR ({score_expr} = %s AND {created_field} < %s)
            OR ({score_expr} = %s AND {created_field} = %s AND {name_field} < %s)
        )
    """
    return clause, (ranking_score, ranking_score, created_on, ranking_score, created_on, name)


# SHORT SERIALIZATION HELPERS
def serialize_short_row(
    row: dict[str, Any],
    *,
    viewer_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Serialize a short row into the public API shape.

    Notes:
    - owner is the creator/poster user.
    - seller is optional shop/seller context and is nested under creator.
    - ad is optional product/ad context.
    - audience controls who can view the short.
    - allow_comments controls whether new comments/replies are allowed.
    - viewer_state should be precomputed by API/service layer to avoid N+1 queries.
    """
    status = row.get("status")
    owner = row.get("owner")
    creator_display = get_user_display(owner)
    creator_is_deleted = bool(creator_display.get("is_deleted"))

    return {
        "id": row.get("name"),
        "status": status,
        "visibility_status": row.get("visibility_status"),
        "content_mode": row.get("content_mode") or DEFAULT_SHORT_CONTENT_MODE,
        "audience": row.get("audience") or DEFAULT_SHORT_AUDIENCE,
        "allow_comments": bool(
            cint(row.get("allow_comments", DEFAULT_ALLOW_COMMENTS))
        ),
        "allow_downloads": bool(
            cint(row.get("allow_downloads", DEFAULT_ALLOW_DOWNLOADS))
        ),
        "is_ready": status == "ready",
        "is_processing": status in ("initialized", "uploaded", "processing"),
        "is_failed": status == "failed",
        "caption": row.get("caption") or "",
        "hashtags": parse_json_if_needed(row.get("hashtags"), default=[]),
        "playback_url": row.get("playback_url"),
        "processed_file_url": row.get("processed_file_url"),
        "raw_video_media": row.get("raw_video_media"),
        "raw_video_media_id": row.get("raw_video_media"),
        "thumbnail_media": row.get("thumbnail_media"),
        "thumbnail_media_id": row.get("thumbnail_media"),
        "audio_mix_status": row.get("audio_mix_status") or "none",
        "audio_mix_error": row.get("audio_mix_error"),
        "thumbnail_url": row.get("thumbnail_url"),
        "duration_seconds": flt(row.get("duration_seconds") or 0),
        "view_count": cint(row.get("view_count") or 0),
        "view_count_display": humanize_count(row.get("view_count") or 0),
        "like_count": cint(row.get("like_count") or 0),
        "like_count_display": humanize_count(row.get("like_count") or 0),
        "comment_count": cint(row.get("comment_count") or 0),
        "comment_count_display": humanize_count(row.get("comment_count") or 0),
        "share_count": cint(row.get("share_count") or 0),
        "share_count_display": humanize_count(row.get("share_count") or 0),
        "save_count": cint(row.get("save_count") or 0),
        "save_count_display": humanize_count(row.get("save_count") or 0),
        "download_count": cint(row.get("download_count") or 0),
        "download_count_display": humanize_count(row.get("download_count") or 0),
        "repost_count": cint(row.get("repost_count") or 0),
        "repost_count_display": humanize_count(row.get("repost_count") or 0),
        "impression_count": cint(row.get("impression_count") or 0),
        "impression_count_display": humanize_count(row.get("impression_count") or 0),
        "ranking_score": flt(row.get("ranking_score") or 0),
        "posted_on": row.get("posted_on"),
        "mentions": row.get("mentions") or [],
        "sound": row.get("sound"),
        "creator": {
            "user": creator_display.get("user"),
            "display_name": creator_display.get("display_name"),
            "avatar": creator_display.get("avatar"),
            "is_deleted": creator_is_deleted,
            "is_live": bool(creator_display.get("is_live")) if not creator_is_deleted else False,
            "live_id": creator_display.get("live_id") if not creator_is_deleted else None,
            "live_status": creator_display.get("live_status") if not creator_is_deleted else None,
            "live_title": creator_display.get("live_title") if not creator_is_deleted else None,
            "live_cover_image": creator_display.get("live_cover_image") if not creator_is_deleted else None,
            "live_started_at": creator_display.get("live_started_at") if not creator_is_deleted else None,
            "live_viewer_count": int(creator_display.get("live_viewer_count") or 0) if not creator_is_deleted else 0,
            "live_viewer_count_display": humanize_count(creator_display.get("live_viewer_count") or 0) if not creator_is_deleted else "0",
            "is_verified": bool(row.get("creator_is_verified")) if not creator_is_deleted else False,
            "seller": (
                {
                    "id": public_seller_id_for_name(row.get("seller")),
                }
                if row.get("seller")
                else None
            ),
        },
        "ad": (
            {
                "id": row.get("ad"),
                "title": row.get("ad_title"),
                "price": row.get("ad_price"),
                "currency": row.get("ad_currency"),
                "thumbnail": row.get("ad_thumbnail"),
            }
            if row.get("ad")
            else None
        ),
        "viewer_state": viewer_state
        or default_short_viewer_state(target_user=owner),
    }


def serialize_comment_row(
    row: dict[str, Any],
    *,
    viewer_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    user = row.get("user")
    author_display = get_user_display(user)

    return {
        "id": row.get("name"),
        "short": row.get("short"),
        "user": author_display.get("user"),
        "seller": public_seller_id_for_name(row.get("seller")) if row.get("seller") else None,
        "display_name": author_display.get("display_name"),
        "avatar": author_display.get("avatar"),
        "is_deleted_user": bool(author_display.get("is_deleted")),
        "is_live": bool(author_display.get("is_live")) if not bool(author_display.get("is_deleted")) else False,
        "live_id": author_display.get("live_id") if not bool(author_display.get("is_deleted")) else None,
        "live_status": author_display.get("live_status") if not bool(author_display.get("is_deleted")) else None,
        "comment": row.get("comment") or "",
        "parent_comment": row.get("parent_comment"),
        "root_comment": row.get("root_comment"),
        "reply_count": cint(row.get("reply_count") or 0),
        "reply_count_display": humanize_count(row.get("reply_count") or 0),
        "like_count": cint(row.get("like_count") or 0),
        "like_count_display": humanize_count(row.get("like_count") or 0),
        "status": row.get("status"),
        "mentions": row.get("mentions") or [],
        "created_at": row.get("creation"),
        "viewer_state": viewer_state or default_comment_viewer_state(),
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
