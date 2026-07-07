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

from typing import Any

import frappe

from aos.api.shared.auth import optional_active_user, require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import fail, ok
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.validators import require_id
from aos.services.live_analytics_service import LiveAnalyticsService

from .activity import (
    hide_live_comment_activity,
    record_live_comment_activity,
)
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
    validate_live_participant_session,
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
    return str(
        value or ""
    ).strip()


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
            code="NOT_FOUND",
        )

    return message, None


# MESSAGE VALIDATION HELPERS
def _validate_comment_message(
    message,
):
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


def _validate_message_active(
    message,
):
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
            code="FORBIDDEN",
        )

    if message.user == user:
        return None

    if live.host_user == user:
        return None

    return fail(
        "You are not allowed to delete this comment.",
        code="FORBIDDEN",
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

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    content = _normalize_content(
        kwargs.get("content")
    )

    if not content:
        return fail(
            "content is required.",
            code="VALIDATION_ERROR",
        )

    try:
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

        message = _create_comment_message(
            live_id=live_id,
            user=user,
            content=content,
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
        frappe.db.rollback()

        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

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

    session_id = _normalize_session_id(
        kwargs.get("session_id")
    )

    content = _normalize_content(
        kwargs.get("content")
    )

    if not content:
        return fail(
            "content is required.",
            code="VALIDATION_ERROR",
        )

    try:
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

        message = _create_comment_message(
            live_id=live_id,
            user=user,
            content=content,
            parent_message=parent_id,
        )

        record_live_comment_activity(
            user=user,
            live_id=live_id,
            message_id=message.name,
            content=content,
            parent_message_id=parent_id,
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

        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

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
        live, err = validate_live_exists(
            live_id
        )
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
            "parent_message": [
                "is",
                "not set",
            ],
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
                    "has_more": (
                        len(rows) == limit
                    ),
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

        err = _validate_comment_message(
            parent
        )
        if err:
            return err

        err = _validate_message_active(
            parent
        )
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
                    "has_more": (
                        len(rows) == limit
                    ),
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
    Collect non-deleted descendants recursively.

    This prevents active or hidden child replies from remaining accessible
    after their parent message is soft-deleted.
    """

    collected: list[str] = []
    seen: set[str] = {
        message_id,
    }
    pending: list[str] = [
        message_id,
    ]

    while pending:
        parent_ids = list(
            pending
        )

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

            seen.add(
                child_id
            )

            collected.append(
                child_id
            )

            pending.append(
                child_id
            )

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
            int(
                active_reply_count or 0
            ),
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
            "message_ids": tuple(
                message_ids
            ),
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

        return safe_fail_from_exception(ex, fallback="Invalid request.", code="VALIDATION_ERROR")

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
