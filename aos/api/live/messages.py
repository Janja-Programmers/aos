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

Live messages include:
- Viewer comments
- Comment replies
- System messages
- Co-host events
- Future gift and moderation events

Rules:
- Guests can read viewer-visible messages.
- Only logged-in users can comment, reply, or delete comments.
- Comment authors can delete their own comments.
- The live host can delete comments in their own live.
- Only comment messages can have replies.
- System and co-host messages are created internally.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.services.live_analytics_service import LiveAnalyticsService

from .constants import (
    ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IP,
    LIST_REPLIES_LIMIT_PER_MINUTE_PER_IP,
    REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER,
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
)


LIVE_MESSAGE_DOCTYPE = "AOS Live Message"
LIVE_STREAM_DOCTYPE = "AOS Live Stream"

ACTIVE_STATUS = "active"
DELETED_STATUS = "deleted"

COMMENT_KIND = "comment"
SYSTEM_KIND = "system"

COMMENT_TYPE = "comment"
REPLY_TYPE = "reply"

DEFAULT_MESSAGES_LIMIT = 20
MAX_MESSAGES_LIMIT = 50

DEFAULT_REPLIES_LIMIT = 50
MAX_REPLIES_LIMIT = 100

GUEST_USER = "Guest"


# PAGINATION
def _parse_pagination(
    kwargs,
    *,
    default_limit: int,
    max_limit: int,
) -> tuple[int, int]:
    limit = int(kwargs.get("limit") or default_limit)
    limit = max(1, min(limit, max_limit))

    start = int(kwargs.get("start") or 0)
    start = max(0, start)

    return start, limit


# SESSION / LIVE HELPERS
def _get_optional_current_user() -> str | None:
    user = frappe.session.user

    if not user or user == GUEST_USER:
        return None

    return user


def _get_live_host(live) -> str | None:
    """
    Return the live host from either a Frappe Document or frappe._dict.
    """

    return getattr(live, "host_user", None)


def _is_live_host(
    live,
    user: str | None,
) -> bool:
    host_user = _get_live_host(live)

    return bool(
        user
        and host_user
        and user == host_user
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
            code="NOT_FOUND",
        )

    return message, None


# MESSAGE VALIDATION HELPERS
def _validate_comment_message(message):
    if message.message_kind != COMMENT_KIND:
        return fail(
            "Only comment messages can be used for this action.",
            code="VALIDATION_ERROR",
        )

    if message.message_type not in {
        COMMENT_TYPE,
        REPLY_TYPE,
    }:
        return fail(
            "Invalid comment message type.",
            code="VALIDATION_ERROR",
        )

    return None


def _validate_message_active(message):
    if message.status != ACTIVE_STATUS:
        return fail(
            "Live message is not active.",
            code="VALIDATION_ERROR",
        )

    return None


def _validate_message_belongs_to_live(
    message,
    live_id: str,
):
    if message.live_stream != live_id:
        return fail(
            "Live message does not belong to this live stream.",
            code="VALIDATION_ERROR",
        )

    return None


def _validate_user_can_delete_message(
    message,
    user: str,
):
    """
    Only user comments can be deleted through this public endpoint.

    System, co-host, gift, and moderation messages must be managed by their
    respective internal services.
    """
    if message.message_kind != COMMENT_KIND:
        return fail(
            "Only viewer comments can be deleted through this endpoint.",
            code="FORBIDDEN",
        )

    if message.user == user:
        return None

    live = frappe.db.get_value(
        LIVE_STREAM_DOCTYPE,
        message.live_stream,
        [
            "name",
            "host_user",
        ],
        as_dict=True,
    )

    if not live:
        return fail(
            "Live stream not found.",
            code="NOT_FOUND",
        )

    if live.host_user == user:
        return None

    return fail(
        "You are not allowed to delete this comment.",
        code="FORBIDDEN",
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
):
    """
    Canonical internal creator for AOS Live Message.

    The DocType controller remains responsible for validating:
    - kind/type compatibility
    - user requirements
    - parent/root integrity
    - live state
    - metadata shape
    """

    message = frappe.new_doc(LIVE_MESSAGE_DOCTYPE)

    message.live_stream = live_id
    message.message_kind = message_kind
    message.message_type = message_type
    message.user = user
    message.target_user = target_user
    message.content = str(content or "").strip()
    message.status = ACTIVE_STATUS
    message.parent_message = parent_message
    message.visible_to_host = int(bool(visible_to_host))
    message.visible_to_viewers = int(bool(visible_to_viewers))

    if metadata:
        message.metadata_json = frappe.as_json(metadata)

    message.insert(ignore_permissions=True)

    return message


def _create_comment_message(
    *,
    live_id: str,
    user: str,
    content: str,
    parent_message: str | None = None,
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
    )


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

    serialized = serialize_live_message(message)

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

    rl = rate_limit(
        key=f"aos:live:message:add:user:{user}",
        ttl_seconds=60,
        limit=ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    content = str(
        kwargs.get("content") or ""
    ).strip()

    if not content:
        return fail(
            "content is required.",
            code="VALIDATION_ERROR",
        )

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        message = _create_comment_message(
            live_id=live_id,
            user=user,
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
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Add Live Message Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to add message.",
            code="INTERNAL_ERROR",
        )


# REPLY TO LIVE MESSAGE
def reply_live_message_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:message:reply:user:{user}",
        ttl_seconds=60,
        limit=REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many replies.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    parent_id, err = require_id(
        kwargs.get("parent_message"),
        "parent_message",
    )
    if err:
        return err

    content = str(
        kwargs.get("content") or ""
    ).strip()

    if not content:
        return fail(
            "content is required.",
            code="VALIDATION_ERROR",
        )

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        parent, err = _get_live_message(
            parent_id
        )
        if err:
            return err

        err = _validate_comment_message(parent)
        if err:
            return err

        err = _validate_message_belongs_to_live(
            parent,
            live_id,
        )
        if err:
            return err

        err = _validate_message_active(parent)
        if err:
            return err

        message = _create_comment_message(
            live_id=live_id,
            user=user,
            content=content,
            parent_message=parent_id,
        )

        serialized = serialize_live_message(
            message
        )

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
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Reply Live Message Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to add reply.",
            code="INTERNAL_ERROR",
        )


# LIST LIVE MESSAGES
def list_live_messages_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:message:list:ip:{ip}",
        ttl_seconds=60,
        limit=LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(
        kwargs.get("live_id"),
        "live_id",
    )
    if err:
        return err

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        start, limit = _parse_pagination(
            kwargs,
            default_limit=DEFAULT_MESSAGES_LIMIT,
            max_limit=MAX_MESSAGES_LIMIT,
        )

        current_user = _get_optional_current_user()
        is_host = _is_live_host(
            live,
            current_user,
        )

        filters: dict[str, Any] = {
            "live_stream": live_id,
            "parent_message": ["is", "not set"],
            "status": ACTIVE_STATUS,
        }

        if is_host:
            filters["visible_to_host"] = 1
        else:
            filters["visible_to_viewers"] = 1

        rows = frappe.get_all(
            LIVE_MESSAGE_DOCTYPE,
            filters=filters,
            fields=live_message_fields(),
            order_by="creation desc",
            limit_start=start,
            limit_page_length=limit,
        )

        return ok(
            "Live messages fetched.",
            data={
                "items": serialize_live_messages(
                    rows
                ),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(rows),
                    "has_more": len(rows) == limit,
                },
            },
        )

    except ValueError:
        return fail(
            "Invalid pagination values.",
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "List Live Messages Failed",
        )

        return fail(
            "Failed to fetch live messages.",
            code="INTERNAL_ERROR",
        )


# LIST LIVE REPLIES
def list_live_replies_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:message:replies:ip:{ip}",
        ttl_seconds=60,
        limit=LIST_REPLIES_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    parent_id, err = require_id(
        kwargs.get("parent_message"),
        "parent_message",
    )
    if err:
        return err

    try:
        parent, err = _get_live_message(
            parent_id
        )
        if err:
            return err

        err = _validate_comment_message(parent)
        if err:
            return err

        err = _validate_message_active(parent)
        if err:
            return err

        start, limit = _parse_pagination(
            kwargs,
            default_limit=DEFAULT_REPLIES_LIMIT,
            max_limit=MAX_REPLIES_LIMIT,
        )

        rows = frappe.get_all(
            LIVE_MESSAGE_DOCTYPE,
            filters={
                "parent_message": parent_id,
                "message_kind": COMMENT_KIND,
                "message_type": REPLY_TYPE,
                "status": ACTIVE_STATUS,
                "visible_to_viewers": 1,
            },
            fields=live_message_fields(),
            order_by="creation asc",
            limit_start=start,
            limit_page_length=limit,
        )

        return ok(
            "Live replies fetched.",
            data={
                "items": serialize_live_messages(
                    rows
                ),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(rows),
                    "has_more": len(rows) == limit,
                },
            },
        )

    except ValueError:
        return fail(
            "Invalid pagination values.",
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "List Live Replies Failed",
        )

        return fail(
            "Failed to fetch live replies.",
            code="INTERNAL_ERROR",
        )


# DELETE HELPERS
def _collect_descendant_message_ids(
    message_id: str,
) -> list[str]:
    """
    Collect active or hidden descendants recursively.

    This prevents child replies from remaining accessible after their parent
    message is deleted.
    """

    collected: list[str] = []
    seen: set[str] = {message_id}
    pending: list[str] = [message_id]

    while pending:
        parent_ids = list(pending)
        pending.clear()

        children = frappe.get_all(
            LIVE_MESSAGE_DOCTYPE,
            filters={
                "parent_message": [
                    "in",
                    parent_ids,
                ],
                "status": [
                    "!=",
                    DELETED_STATUS,
                ],
            },
            pluck="name",
        )

        for child_id in children:
            if child_id in seen:
                continue

            seen.add(child_id)
            collected.append(child_id)
            pending.append(child_id)

    return collected


def _sync_reply_counts(
    parent_ids: set[str],
):
    for parent_id in parent_ids:
        if not parent_id:
            continue

        if not frappe.db.exists(
            LIVE_MESSAGE_DOCTYPE,
            parent_id,
        ):
            continue

        active_reply_count = frappe.db.count(
            LIVE_MESSAGE_DOCTYPE,
            filters={
                "parent_message": parent_id,
                "message_kind": COMMENT_KIND,
                "message_type": REPLY_TYPE,
                "status": ACTIVE_STATUS,
            },
        )

        frappe.db.set_value(
            LIVE_MESSAGE_DOCTYPE,
            parent_id,
            "reply_count",
            active_reply_count,
            update_modified=False,
        )


def _soft_delete_messages(
    message_ids: list[str],
):
    if not message_ids:
        return

    frappe.db.sql(
        """
        UPDATE `tabAOS Live Message`
        SET status = %(status)s
        WHERE name IN %(message_ids)s
        """,
        {
            "status": DELETED_STATUS,
            "message_ids": tuple(message_ids),
        },
    )


# DELETE LIVE MESSAGE
def delete_live_message_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:message:delete:user:{user}",
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

        err = _validate_user_can_delete_message(
            message,
            user,
        )
        if err:
            return err

        if message.status == DELETED_STATUS:
            return ok(
                "Message already deleted.",
                data={
                    "message_id": message_id,
                    "deleted_message_ids": [
                        message_id
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
                "parent_message",
            ],
        )

        affected_parent_ids = {
            row.parent_message
            for row in affected_rows
            if row.parent_message
            and row.parent_message
            not in deleted_ids
        }

        _soft_delete_messages(
            deleted_ids
        )

        _sync_reply_counts(
            affected_parent_ids
        )

        LiveAnalyticsService.sync_comment_count(
            live_id=message.live_stream,
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
        frappe.db.rollback()

        return fail(
            str(ex),
            code="VALIDATION_ERROR",
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Delete Live Message Failed",
        )
        frappe.db.rollback()

        return fail(
            "Failed to delete message.",
            code="INTERNAL_ERROR",
        )
