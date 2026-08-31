"""
Live Message APIs and internal message helpers.

Handles public APIs:
- add_live_message
- reply_live_message
- list_live_messages
- list_live_replies
- delete_live_message

Handles internal creation:
- create_live_system_message
- create_live_cohost_message

Live messages include:
- Viewer comments
- Comment replies
- System messages
- Co-host events
- Future gift and moderation events

Rules:
- Guests can read viewer-visible messages.
- Only logged-in users can comment, reply, or delete comments.
- Non-host viewers must own an active view session to comment or reply.
- Comment authors can soft-delete their own comments while the live is active.
- The live host can soft-delete comments while their live is active.
- Only comment messages can have replies.
- System and co-host messages are created internally.
- Private messages are stored without public visibility and delivered through
  targeted realtime by the calling service.
- Administrative hard deletion is handled by the DocType controller.
"""

from __future__ import annotations

import html
import unicodedata
from typing import Any

import frappe

from aos.api.shared.auth import optional_active_user, require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.api.shared.db import is_duplicate_entry_error
from aos.services.live.cursor import decode_cursor, encode_cursor
from aos.services.live.errors import LiveError
from aos.services.live.repository import LiveRepository
from aos.services.live_analytics_service import LiveAnalyticsService

from .activity import (
    hide_live_comment_activity,
    record_live_comment_activity,
)
from .constants import (
    ADD_COMMENT_GLOBAL_LIMIT_PER_MINUTE_PER_USER,
    ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IDENTITY,
    LIST_REPLIES_LIMIT_PER_MINUTE_PER_IDENTITY,
    LIST_COMMENTS_GLOBAL_LIMIT_PER_MINUTE_PER_IDENTITY,
    LIST_REPLIES_GLOBAL_LIMIT_PER_MINUTE_PER_IDENTITY,
    REPLY_COMMENT_GLOBAL_LIMIT_PER_MINUTE_PER_USER,
    REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    LIVE_COMMENT_FANOUT_LIMIT_PER_MINUTE,
)
from .realtime import (
    publish_live_message,
    publish_live_message_deleted,
)
from .serializers import (
    live_message_fields,
    serialize_live_message,
    serialize_live_messages,
)
from .validators import (
    validate_live_active,
    validate_live_exists,
    validate_live_participant_session,
    validate_live_social_access,
)


LIVE_MESSAGE_DOCTYPE = "AOS Live Message"

ACTIVE_STATUS = "active"
DELETED_STATUS = "deleted"

COMMENT_KIND = "comment"
SYSTEM_KIND = "system"
COHOST_KIND = "cohost"

COMMENT_TYPE = "comment"
REPLY_TYPE = "reply"

VALID_SYSTEM_MESSAGE_TYPES = {
    "live_started",
    "notifying_followers",
    "viewer_joined",
    "live_ended",
    "generic",
}

VALID_COHOST_MESSAGE_TYPES = {
    "cohost_invited",
    "cohost_request_sent",
    "cohost_request_accepted",
    "cohost_request_rejected",
    "cohost_started",
    "cohost_ended",
    "generic",
}

DEFAULT_MESSAGES_LIMIT = 20
MAX_MESSAGES_LIMIT = 50

DEFAULT_REPLIES_LIMIT = 50
MAX_REPLIES_LIMIT = 100

GUEST_USER = "Guest"
MAX_DELETE_DESCENDANTS = 5000


# GENERIC HELPERS
def _normalize_session_id(
    value,
) -> str | None:
    session_id = str(
        value or ""
    ).strip()

    return session_id or None


def _normalize_message_type(
    value,
) -> str:
    return str(
        value or ""
    ).strip().lower()


def _normalize_content(
    value,
) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if len(text) > 500:
        raise ValueError("Live comment is too long.")
    if "\x00" in text:
        raise ValueError("Invalid Live comment.")
    return html.escape(text, quote=False)


def _normalize_idempotency_key(value) -> str | None:
    key = str(value or "").strip()
    if not key:
        return None
    if len(key) > 128 or any(ord(ch) < 32 for ch in key):
        raise ValueError("Invalid idempotency key.")
    return key


def _parse_pagination(
    kwargs,
    *,
    default_limit: int,
    max_limit: int,
) -> tuple[int, int]:
    limit = int(
        kwargs.get("limit")
        or default_limit
    )

    limit = max(
        1,
        min(
            limit,
            max_limit,
        ),
    )

    start = int(
        kwargs.get("start")
        or 0
    )

    start = max(
        0,
        start,
    )

    return start, limit


def _parse_bool(value) -> bool:
    """Parse an API boolean without treating every non-empty string as true."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


# SESSION / LIVE HELPERS
def _get_optional_current_user() -> str | None:
    return optional_active_user()


def _is_live_host(
    live,
    user: str | None,
) -> bool:
    return bool(
        user
        and live.host_user
        and user == live.host_user
    )


# MESSAGE FETCHING
def _get_live_message(
    message_id: str,
    *,
    fields: list[str] | None = None,
):
    message = frappe.db.get_value(
        LIVE_MESSAGE_DOCTYPE,
        message_id,
        fields or live_message_fields(),
        as_dict=True,
    )

    if not message:
        return None, fail(
            "Live message not found.",
            error="NOT_FOUND",
        )

    return message, None


# MESSAGE VALIDATION HELPERS
def _validate_comment_message(
    message,
):
    if message.message_kind != COMMENT_KIND:
        return fail(
            "Only comment messages can be used for this action.",
            error="VALIDATION_ERROR",
        )

    if message.message_type not in {
        COMMENT_TYPE,
        REPLY_TYPE,
    }:
        return fail(
            "Invalid comment message type.",
            error="VALIDATION_ERROR",
        )

    return None


def _validate_message_active(
    message,
):
    if message.status != ACTIVE_STATUS:
        return fail(
            "Live message is not active.",
            error="VALIDATION_ERROR",
        )

    return None


def _validate_message_belongs_to_live(
    message,
    live_id: str,
):
    if message.live_stream != live_id:
        return fail(
            "Live message does not belong to this live stream.",
            error="VALIDATION_ERROR",
        )

    return None


def _validate_user_can_delete_message(
    *,
    message,
    live,
    user: str,
):
    """
    Only comment messages can be soft-deleted through the public endpoint.

    System, co-host, gift, and moderation messages are managed by their
    respective internal services.
    """
    if message.message_kind != COMMENT_KIND:
        return fail(
            "Only viewer comments can be deleted through this endpoint.",
            error="FORBIDDEN",
        )

    if message.user == user:
        return None

    if live.host_user == user:
        return None

    return fail(
        "You are not allowed to delete this comment.",
        error="FORBIDDEN",
    )


def _validate_internal_message_type(
    *,
    message_kind: str,
    message_type: str,
):
    if (
        message_kind == SYSTEM_KIND
        and message_type not in VALID_SYSTEM_MESSAGE_TYPES
    ):
        frappe.throw(
            f"Invalid system message type: {message_type}."
        )

    if (
        message_kind == COHOST_KIND
        and message_type not in VALID_COHOST_MESSAGE_TYPES
    ):
        frappe.throw(
            f"Invalid co-host message type: {message_type}."
        )


# INTERNAL MESSAGE CREATION
def _create_live_message(
    *,
    live_id: str,
    message_kind: str,
    message_type: str,
    content: str,
    user: str | None = None,
    target_user: str | None = None,
    parent_message: str | None = None,
    metadata: dict | None = None,
    visible_to_host: bool = True,
    visible_to_viewers: bool = True,
    idempotency_key: str | None = None,
):
    """
    Canonical internal creator for AOS Live Message.

    The DocType controller remains responsible for validating:
    - message kind/type compatibility
    - actor requirements
    - target-user requirements
    - parent/root integrity
    - live state
    - metadata shape
    - visibility rules
    """

    normalized_kind = str(
        message_kind or ""
    ).strip().lower()

    normalized_type = _normalize_message_type(
        message_type
    )

    normalized_content = _normalize_content(
        content
    )

    if not live_id:
        frappe.throw(
            "Live stream is required."
        )

    if not normalized_kind:
        frappe.throw(
            "Message kind is required."
        )

    if not normalized_type:
        frappe.throw(
            "Message type is required."
        )

    if not normalized_content:
        frappe.throw(
            "Message content is required."
        )

    _validate_internal_message_type(
        message_kind=normalized_kind,
        message_type=normalized_type,
    )

    if metadata is not None and not isinstance(
        metadata,
        dict,
    ):
        frappe.throw(
            "Message metadata must be a dictionary."
        )

    message = frappe.new_doc(
        LIVE_MESSAGE_DOCTYPE
    )

    message.live_stream = live_id
    message.message_kind = normalized_kind
    message.message_type = normalized_type
    message.user = user
    message.target_user = target_user
    message.content = normalized_content
    message.status = ACTIVE_STATUS
    message.parent_message = parent_message
    message.visible_to_host = int(
        bool(visible_to_host)
    )
    message.visible_to_viewers = int(
        bool(visible_to_viewers)
    )
    if hasattr(message, "idempotency_key"):
        message.idempotency_key = idempotency_key

    if metadata is not None:
        message.metadata_json = frappe.as_json(
            metadata
        )

    message.insert(
        ignore_permissions=True
    )

    return message


def _create_comment_message(
    *,
    live_id: str,
    user: str,
    content: str,
    parent_message: str | None = None,
    idempotency_key: str | None = None,
):
    return _create_live_message(
        live_id=live_id,
        message_kind=COMMENT_KIND,
        message_type=(
            REPLY_TYPE
            if parent_message
            else COMMENT_TYPE
        ),
        user=user,
        content=content,
        parent_message=parent_message,
        visible_to_host=True,
        visible_to_viewers=True,
        idempotency_key=idempotency_key,
    )


def _find_idempotent_comment(*, live_id: str, user: str, key: str | None):
    if not key:
        return None
    return frappe.db.get_value(
        LIVE_MESSAGE_DOCTYPE,
        {
            "live_stream": live_id,
            "user": user,
            "idempotency_key": key,
            "message_kind": COMMENT_KIND,
            "status": ACTIVE_STATUS,
        },
        live_message_fields(),
        as_dict=True,
    )


def _create_comment_idempotently(
    *,
    live_id: str,
    user: str,
    content: str,
    parent_message: str | None,
    idempotency_key: str | None,
):
    try:
        return (
            _create_comment_message(
                live_id=live_id,
                user=user,
                content=content,
                parent_message=parent_message,
                idempotency_key=idempotency_key,
            ),
            False,
        )
    except Exception as exc:
        if not idempotency_key or not is_duplicate_entry_error(exc):
            raise
        existing = _find_idempotent_comment(
            live_id=live_id,
            user=user,
            key=idempotency_key,
        )
        if not existing:
            raise
        return existing, True


_MESSAGE_SELECT = """
    name, live_stream, message_kind, message_type, `user`, target_user,
    content, metadata_json, status, parent_message, root_message, reply_count,
    visible_to_host, visible_to_viewers, creation, modified
"""


def _serialize_messages_with_reply_context(messages: list) -> list[dict]:
    """Serialize a message page and batch-attach immediate parent context.

    LIVE chat clients can render replies inline (TikTok-style) without issuing a
    second request per thread.  The legacy root-only list and list_live_replies
    APIs remain supported.
    """
    items = serialize_live_messages(messages)
    parent_ids = {
        str(item.get("parent_message") or "").strip()
        for item in items
        if item.get("is_reply") and item.get("parent_message")
    }
    parent_ids.discard("")
    if not parent_ids:
        return items

    parent_rows = frappe.get_all(
        LIVE_MESSAGE_DOCTYPE,
        filters={"name": ["in", sorted(parent_ids)]},
        fields=live_message_fields(),
    )
    parent_map = {
        str(item.get("message_id")): item
        for item in serialize_live_messages(parent_rows)
    }

    for item in items:
        parent_id = str(item.get("parent_message") or "").strip()
        parent = parent_map.get(parent_id)
        if not parent:
            continue
        item["reply_to"] = {
            "message_id": parent.get("message_id"),
            "user": parent.get("user"),
            "display_name": parent.get("display_name"),
            "avatar": parent.get("avatar"),
            "is_verified": bool(parent.get("is_verified")),
            "content": parent.get("content") or "",
        }

    return items


def _message_cursor(value: str | None, *, kind: str, scope: str):
    if not value:
        return None
    payload = decode_cursor(value)
    if payload.get("kind") != kind or payload.get("scope") != scope:
        raise ValueError("cursor_scope")
    creation = str(payload.get("creation") or "")
    name = str(payload.get("name") or "")
    if not creation or not name:
        raise ValueError("cursor_position")
    return creation, name


def create_live_system_message(
    *,
    live_id: str,
    message_type: str,
    content: str,
    user: str | None = None,
    target_user: str | None = None,
    metadata: dict | None = None,
    visible_to_host: bool = True,
    visible_to_viewers: bool = True,
    publish: bool = True,
) -> dict:
    """
    Create and optionally publish a system message.

    This is an internal helper, not a public API endpoint.

    Examples:
    - live_started
    - notifying_followers
    - viewer_joined
    - live_ended

    Private messages must use publish=False and should be delivered through
    publish_live_message_to_user() by the calling service.
    """
    message = _create_live_message(
        live_id=live_id,
        message_kind=SYSTEM_KIND,
        message_type=message_type,
        user=user,
        target_user=target_user,
        content=content,
        metadata=metadata,
        visible_to_host=visible_to_host,
        visible_to_viewers=visible_to_viewers,
    )

    serialized = serialize_live_message(
        message
    )

    if publish:
        publish_live_message(
            live_id,
            serialized,
        )

    return serialized


def create_live_cohost_message(
    *,
    live_id: str,
    message_type: str,
    content: str,
    user: str | None = None,
    target_user: str | None = None,
    metadata: dict | None = None,
    visible_to_host: bool = False,
    visible_to_viewers: bool = False,
    publish: bool = False,
) -> dict:
    """
    Create and optionally publish a co-host workflow or lifecycle message.

    Private workflow examples:
    - cohost_invited
    - cohost_request_sent
    - cohost_request_accepted
    - cohost_request_rejected

    Public lifecycle examples:
    - cohost_started
    - cohost_ended

    Privacy contract:
    - Private co-host messages should use publish=False.
    - The calling co-host service delivers private messages through targeted
      realtime.
    - Public lifecycle messages may use publish=True and must be visible to
      both host and viewers.
    """
    normalized_type = _normalize_message_type(
        message_type
    )

    if normalized_type not in VALID_COHOST_MESSAGE_TYPES:
        frappe.throw(
            f"Invalid co-host message type: {normalized_type}."
        )

    if publish and not visible_to_viewers:
        frappe.throw(
            "A room-published co-host message must be visible to viewers."
        )

    message = _create_live_message(
        live_id=live_id,
        message_kind=COHOST_KIND,
        message_type=normalized_type,
        user=user,
        target_user=target_user,
        content=content,
        metadata=metadata,
        visible_to_host=visible_to_host,
        visible_to_viewers=visible_to_viewers,
    )

    serialized = serialize_live_message(
        message
    )

    if publish:
        publish_live_message(
            live_id,
            serialized,
        )

    return serialized


# ADD LIVE MESSAGE
def add_live_message_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    global_rl = rate_limit(
        key=rate_limit_key("live", "message", "add", "global", "user", user),
        ttl_seconds=60,
        limit=ADD_COMMENT_GLOBAL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if global_rl:
        return global_rl

    room_rl = rate_limit(
        key=rate_limit_key("live", "message", "add", live_id, "user", user),
        ttl_seconds=60,
        limit=ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if room_rl:
        return room_rl

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        content = _normalize_content(kwargs.get("content"))
        idempotency_key = _normalize_idempotency_key(kwargs.get("idempotency_key"))
    except ValueError:
        return fail("Invalid comment content.", error="VALIDATION_ERROR")

    if not content:
        return fail(
            "content is required.",
            error="VALIDATION_ERROR",
        )

    try:
        LiveRepository().lock_live_shared(live_id)
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        err = validate_live_participant_session(
            live=live,
            user=user,
            session_id=session_id,
        )
        if err:
            return err

        existing = _find_idempotent_comment(live_id=live_id, user=user, key=idempotency_key)
        if existing:
            return ok("Message already added.", data={"message": serialize_live_message(existing)})

        room_limit = rate_limit(
            key=rate_limit_key("live", "message", "fanout", live_id),
            ttl_seconds=60,
            limit=LIVE_COMMENT_FANOUT_LIMIT_PER_MINUTE,
            message="Live chat is moving too quickly. Please try again shortly.",
        )
        if room_limit:
            return room_limit

        message, duplicate = _create_comment_idempotently(
            live_id=live_id,
            user=user,
            content=content,
            parent_message=None,
            idempotency_key=idempotency_key,
        )
        if duplicate:
            return ok(
                "Message already added.",
                data={"message": serialize_live_message(message)},
            )

        record_live_comment_activity(
            user=user,
            live_id=live_id,
            message_id=message.name,
            content=content,
        )

        serialized = serialize_live_message(
            message
        )

        publish_live_message(
            live_id,
            serialized,
        )

        return ok(
            "Message added.",
            data={
                "message": serialized,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Add Live Message Failed",
        )
        return fail(
            "Failed to add message.",
            error="INTERNAL_ERROR",
        )


# REPLY TO LIVE MESSAGE
def reply_live_message_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    global_rl = rate_limit(
        key=rate_limit_key("live", "message", "reply", "global", "user", user),
        ttl_seconds=60,
        limit=REPLY_COMMENT_GLOBAL_LIMIT_PER_MINUTE_PER_USER,
        message="Too many replies.",
    )
    if global_rl:
        return global_rl

    room_rl = rate_limit(
        key=rate_limit_key("live", "message", "reply", live_id, "user", user),
        ttl_seconds=60,
        limit=REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many replies.",
    )
    if room_rl:
        return room_rl

    parent_id, err = require_id(
        kwargs.get("parent_message"),
        "parent_message",
    )
    if err:
        return err

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    try:
        content = _normalize_content(kwargs.get("content"))
        idempotency_key = _normalize_idempotency_key(kwargs.get("idempotency_key"))
    except ValueError:
        return fail("Invalid comment content.", error="VALIDATION_ERROR")

    if not content:
        return fail(
            "content is required.",
            error="VALIDATION_ERROR",
        )

    try:
        LiveRepository().lock_live_shared(live_id)
        live, err = validate_live_exists(
            live_id
        )
        if err:
            return err

        err = validate_live_active(
            live
        )
        if err:
            return err

        err = validate_live_participant_session(
            live=live,
            user=user,
            session_id=session_id,
        )
        if err:
            return err

        parent, err = _get_live_message(
            parent_id
        )
        if err:
            return err

        err = _validate_comment_message(
            parent
        )
        if err:
            return err

        err = _validate_message_belongs_to_live(
            parent,
            live_id,
        )
        if err:
            return err

        err = _validate_message_active(
            parent
        )
        if err:
            return err

        existing = _find_idempotent_comment(live_id=live_id, user=user, key=idempotency_key)
        if existing:
            serialized_existing = _serialize_messages_with_reply_context([existing])[0]
            return ok("Reply already added.", data={"message": serialized_existing})

        room_limit = rate_limit(
            key=rate_limit_key("live", "message", "fanout", live_id),
            ttl_seconds=60,
            limit=LIVE_COMMENT_FANOUT_LIMIT_PER_MINUTE,
            message="Live chat is moving too quickly. Please try again shortly.",
        )
        if room_limit:
            return room_limit

        message, duplicate = _create_comment_idempotently(
            live_id=live_id,
            user=user,
            content=content,
            parent_message=parent_id,
            idempotency_key=idempotency_key,
        )
        if duplicate:
            return ok(
                "Reply already added.",
                data={"message": _serialize_messages_with_reply_context([message])[0]},
            )

        record_live_comment_activity(
            user=user,
            live_id=live_id,
            message_id=message.name,
            content=content,
            parent_message_id=parent_id,
        )

        serialized = _serialize_messages_with_reply_context([message])[0]

        publish_live_message(
            live_id,
            serialized,
        )

        return ok(
            "Reply added.",
            data={
                "message": serialized,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Reply Live Message Failed",
        )
        return fail(
            "Failed to add reply.",
            error="INTERNAL_ERROR",
        )


# LIST LIVE MESSAGES
def list_live_messages_impl(**kwargs):
    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    current_user = _get_optional_current_user()
    rate_kind = "user" if current_user else "ip"
    rate_identity = current_user or request_ip()
    global_rl = rate_limit(
        key=rate_limit_key("live", "message", "list", "global", rate_kind, rate_identity),
        ttl_seconds=60,
        limit=LIST_COMMENTS_GLOBAL_LIMIT_PER_MINUTE_PER_IDENTITY,
        message="Too many requests.",
    )
    if global_rl:
        return global_rl

    room_rl = rate_limit(
        key=rate_limit_key("live", "message", "list", live_id, rate_kind, rate_identity),
        ttl_seconds=60,
        limit=LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IDENTITY,
        message="Too many requests.",
    )
    if room_rl:
        return room_rl

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err
        access_err = validate_live_social_access(live=live, user=current_user)
        if access_err:
            return access_err

        start, limit = _parse_pagination(
            kwargs,
            default_limit=DEFAULT_MESSAGES_LIMIT,
            max_limit=MAX_MESSAGES_LIMIT,
        )
        include_replies = _parse_bool(kwargs.get("include_replies"))
        cursor_kind = "live_messages_all" if include_replies else "live_messages"
        cursor_value = str(kwargs.get("cursor") or "").strip()
        cursor = _message_cursor(cursor_value, kind=cursor_kind, scope=live_id)
        is_host = _is_live_host(live, current_user)
        visibility = "visible_to_host" if is_host else "visible_to_viewers"
        params: dict[str, Any] = {
            "live_id": live_id,
            "limit": limit + 1,
            "offset": 0 if cursor else start,
        }
        cursor_sql = ""
        if cursor:
            params.update({"cursor_creation": cursor[0], "cursor_name": cursor[1]})
            cursor_sql = """
              AND (creation < %(cursor_creation)s
                   OR (creation = %(cursor_creation)s AND name < %(cursor_name)s))
            """
        parent_filter_sql = "" if include_replies else "AND (parent_message IS NULL OR parent_message='')"
        rows = frappe.db.sql(
            f"""
            SELECT {_MESSAGE_SELECT}
            FROM `tabAOS Live Message`
            WHERE live_stream=%(live_id)s
              {parent_filter_sql}
              AND status='active' AND {visibility}=1
              {cursor_sql}
            ORDER BY creation DESC, name DESC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
            as_dict=True,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor({
                "kind": cursor_kind,
                "scope": live_id,
                "creation": str(last.creation),
                "name": str(last.name),
            })
        return ok(
            "Live messages fetched.",
            data={
                "items": _serialize_messages_with_reply_context(page),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(page),
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                },
            },
        )
    except LiveError as exc:
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)
    except ValueError:
        return fail("Invalid Live cursor or pagination values.", error="LIVE_INVALID_CURSOR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Live Messages Failed")
        return fail("Failed to fetch live messages.", error="INTERNAL_ERROR")


# LIST LIVE REPLIES
def list_live_replies_impl(**kwargs):
    parent_id, err = require_id(kwargs.get("parent_message"), "parent_message")
    if err:
        return err

    current_user = _get_optional_current_user()
    rate_kind = "user" if current_user else "ip"
    rate_identity = current_user or request_ip()
    global_rl = rate_limit(
        key=rate_limit_key("live", "message", "replies", "global", rate_kind, rate_identity),
        ttl_seconds=60,
        limit=LIST_REPLIES_GLOBAL_LIMIT_PER_MINUTE_PER_IDENTITY,
        message="Too many requests.",
    )
    if global_rl:
        return global_rl

    thread_rl = rate_limit(
        key=rate_limit_key("live", "message", "replies", parent_id, rate_kind, rate_identity),
        ttl_seconds=60,
        limit=LIST_REPLIES_LIMIT_PER_MINUTE_PER_IDENTITY,
        message="Too many requests.",
    )
    if thread_rl:
        return thread_rl

    try:
        parent, err = _get_live_message(parent_id)
        if err:
            return err
        err = _validate_comment_message(parent)
        if err:
            return err
        err = _validate_message_active(parent)
        if err:
            return err

        live, err = validate_live_exists(parent.live_stream)
        if err:
            return err
        access_err = validate_live_social_access(live=live, user=current_user)
        if access_err:
            return access_err

        start, limit = _parse_pagination(
            kwargs,
            default_limit=DEFAULT_REPLIES_LIMIT,
            max_limit=MAX_REPLIES_LIMIT,
        )
        cursor_value = str(kwargs.get("cursor") or "").strip()
        cursor = _message_cursor(cursor_value, kind="live_replies", scope=parent_id)
        is_host = _is_live_host(live, current_user)
        visibility = "visible_to_host" if is_host else "visible_to_viewers"
        params: dict[str, Any] = {
            "parent_id": parent_id,
            "limit": limit + 1,
            "offset": 0 if cursor else start,
        }
        cursor_sql = ""
        if cursor:
            params.update({"cursor_creation": cursor[0], "cursor_name": cursor[1]})
            cursor_sql = """
              AND (creation > %(cursor_creation)s
                   OR (creation = %(cursor_creation)s AND name > %(cursor_name)s))
            """
        rows = frappe.db.sql(
            f"""
            SELECT {_MESSAGE_SELECT}
            FROM `tabAOS Live Message`
            WHERE parent_message=%(parent_id)s
              AND message_kind='comment' AND message_type='reply'
              AND status='active' AND {visibility}=1
              {cursor_sql}
            ORDER BY creation ASC, name ASC
            LIMIT %(limit)s OFFSET %(offset)s
            """,
            params,
            as_dict=True,
        )
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            last = page[-1]
            next_cursor = encode_cursor({
                "kind": "live_replies",
                "scope": parent_id,
                "creation": str(last.creation),
                "name": str(last.name),
            })
        return ok(
            "Live replies fetched.",
            data={
                "items": _serialize_messages_with_reply_context(page),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(page),
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                },
            },
        )
    except LiveError as exc:
        return fail(exc.public_message, error=exc.code, data=exc.data, http_status=exc.http_status)
    except ValueError:
        return fail("Invalid Live cursor or pagination values.", error="LIVE_INVALID_CURSOR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Live Replies Failed")
        return fail("Failed to fetch live replies.", error="INTERNAL_ERROR")


# DELETE HELPERS
def _collect_descendant_message_ids(message_id: str) -> list[str]:
    """Return descendant replies with an explicit safety bound.

    Live replies may target comments or replies, so deletion must walk the
    whole thread branch. Keep the traversal bounded so a corrupt/cyclic tree
    cannot turn one moderation request into an unbounded database operation.
    """
    if not message_id:
        return []

    collected: list[str] = []
    seen = {message_id}
    pending = [message_id]

    while pending:
        parents = pending
        pending = []
        children = frappe.get_all(
            LIVE_MESSAGE_DOCTYPE,
            filters={"parent_message": ["in", parents]},
            pluck="name",
            limit=MAX_DELETE_DESCENDANTS + 1,
        )
        for child_id in children:
            child_id = str(child_id or "").strip()
            if not child_id or child_id in seen:
                continue
            seen.add(child_id)
            collected.append(child_id)
            if len(collected) > MAX_DELETE_DESCENDANTS:
                frappe.throw("Live message thread is too large to delete in one request.")
            pending.append(child_id)

    return collected


def _soft_delete_messages(message_ids: list[str]) -> None:
    """Soft-delete message rows without invoking per-row Document hooks."""
    ids = list(dict.fromkeys(str(value or "").strip() for value in message_ids if value))
    if not ids:
        return

    placeholders = ", ".join(["%s"] * len(ids))
    frappe.db.sql(
        f"""
        UPDATE `tabAOS Live Message`
        SET status=%s,
            active_idempotency_key=NULL,
            modified=NOW(),
            modified_by=%s
        WHERE name IN ({placeholders})
          AND status<>%s
        """,
        (DELETED_STATUS, frappe.session.user, *ids, DELETED_STATUS),
    )


def _sync_reply_counts(parent_ids: set[str]) -> None:
    """Repair direct active-reply counts for surviving parent messages."""
    ids = sorted({str(value or "").strip() for value in parent_ids if value})
    if not ids:
        return

    placeholders = ", ".join(["%s"] * len(ids))
    rows = frappe.db.sql(
        f"""
        SELECT parent_message, COUNT(*) AS reply_count
        FROM `tabAOS Live Message`
        WHERE parent_message IN ({placeholders})
          AND message_kind=%s
          AND message_type=%s
          AND status=%s
        GROUP BY parent_message
        """,
        (*ids, COMMENT_KIND, REPLY_TYPE, ACTIVE_STATUS),
        as_dict=True,
    )
    counts = {str(row.parent_message): int(row.reply_count or 0) for row in rows}
    for parent_id in ids:
        if frappe.db.exists(LIVE_MESSAGE_DOCTYPE, parent_id):
            frappe.db.set_value(
                LIVE_MESSAGE_DOCTYPE,
                parent_id,
                "reply_count",
                counts.get(parent_id, 0),
                update_modified=False,
            )


# DELETE LIVE MESSAGE
def delete_live_message_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=rate_limit_key("live", "message", "delete", "user", user),
        ttl_seconds=60,
        limit=DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    message_id, err = require_id(
        kwargs.get("message_id"),
        "message_id",
    )
    if err:
        return err

    try:
        message, err = _get_live_message(
            message_id
        )
        if err:
            return err

        live, err = validate_live_exists(
            message.live_stream
        )
        if err:
            return err

        # API soft deletion is available only while the live is active.
        # Administrative hard deletion after the live ends is handled by
        # the AOS Live Message DocType controller.
        err = validate_live_active(
            live
        )
        if err:
            return err

        err = _validate_user_can_delete_message(
            message=message,
            live=live,
            user=user,
        )
        if err:
            return err

        if message.status == DELETED_STATUS:
            return ok(
                "Message already deleted.",
                data={
                    "message_id": message_id,
                    "deleted_message_ids": [
                        message_id,
                    ],
                },
            )

        descendant_ids = (
            _collect_descendant_message_ids(
                message_id
            )
        )

        deleted_ids = list(
            dict.fromkeys(
                [
                    message_id,
                    *descendant_ids,
                ]
            )
        )

        affected_rows = frappe.get_all(
            LIVE_MESSAGE_DOCTYPE,
            filters={
                "name": [
                    "in",
                    deleted_ids,
                ],
            },
            fields=[
                "name",
                "user",
                "parent_message",
                "message_kind",
                "message_type",
                "status",
            ],
        )

        affected_parent_ids = {
            row.parent_message
            for row in affected_rows
            if (
                row.parent_message
                and row.parent_message
                not in deleted_ids
            )
        }

        _soft_delete_messages(
            deleted_ids
        )

        for row in affected_rows:
            hide_live_comment_activity(
                user=row.user,
                message_id=row.name,
            )

        _sync_reply_counts(
            affected_parent_ids
        )

        deleted_comment_count = sum(
            1
            for row in affected_rows
            if row.message_kind == COMMENT_KIND and row.status == ACTIVE_STATUS
        )
        if deleted_comment_count:
            LiveAnalyticsService.handle_comment_deleted(
                live_id=message.live_stream,
                count=deleted_comment_count,
            )

        publish_live_message_deleted(
            message.live_stream,
            message_id,
            deleted_message_ids=deleted_ids,
        )

        return ok(
            "Message deleted.",
            data={
                "message_id": message_id,
                "deleted_message_ids": deleted_ids,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Delete Live Message Failed",
        )
        return fail(
            "Failed to delete message.",
            error="INTERNAL_ERROR",
        )
