"""
Live Comment APIs (implementation).

Handles:
- add_comment
- reply_comment
- list_comments
- list_replies
- delete_comment

Rules:
- Guests can read comments.
- Only logged-in users can comment/reply/delete.
- Comment author can delete their own comment.
- Live host can delete comments on their own live.
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id
from aos.services.live_analytics_service import LiveAnalyticsService

from .constants import (
    ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER,
    LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IP,
    LIST_REPLIES_LIMIT_PER_MINUTE_PER_IP,
    DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER,
)

from .validators import (
    validate_live_exists,
    validate_live_active,
    validate_comment_exists,
    validate_comment_belongs_to_live,
    validate_comment_active,
    validate_user_can_delete_comment,
)

from .realtime import (
    publish_comment,
    publish_comment_deleted,
)

from .serializers import (
    preload_users,
)


ACTIVE_STATUS = "active"
DELETED_STATUS = "deleted"
DEFAULT_COMMENTS_LIMIT = 20
MAX_COMMENTS_LIMIT = 50
DEFAULT_REPLIES_LIMIT = 50
MAX_REPLIES_LIMIT = 100


# SERIALIZATION
def _comment_fields() -> list[str]:
    return [
        "name",
        "live_stream",
        "user",
        "content",
        "status",
        "parent_comment",
        "root_comment",
        "reply_count",
        "creation",
    ]


def _serialize_comment(comment, *, preloaded_user: dict | None = None) -> dict:
    user_payload = preloaded_user

    if user_payload is None:
        users = preload_users([comment.user])
        user_payload = users.get(comment.user)

    user_payload = user_payload or {
        "user": comment.user,
        "display_name": comment.user,
        "avatar": None,
        "is_verified": False,
        "total_followers": 0,
    }

    return {
        "id": comment.name,
        "comment_id": comment.name,
        "live_stream": comment.live_stream,
        "user": user_payload["user"],
        "display_name": user_payload["display_name"],
        "avatar": user_payload["avatar"],
        "is_verified": user_payload["is_verified"],
        "content": comment.content,
        "status": comment.status,
        "parent_comment": comment.parent_comment,
        "root_comment": comment.root_comment,
        "reply_count": int(comment.reply_count or 0),
        "creation": comment.creation,
    }


def _serialize_comments(rows: list) -> list[dict]:
    users = preload_users([row.user for row in rows if row.user])

    return [
        _serialize_comment(
            row,
            preloaded_user=users.get(row.user),
        )
        for row in rows
    ]


def _parse_pagination(kwargs, *, default_limit: int, max_limit: int):
    limit = int(kwargs.get("limit") or default_limit)
    limit = max(1, min(limit, max_limit))

    start = int(kwargs.get("start") or 0)
    start = max(0, start)

    return start, limit


# ADD COMMENT
def add_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:comment:add:user:{user}",
        ttl_seconds=60,
        limit=ADD_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many messages. Please slow down.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    content = (kwargs.get("content") or "").strip()
    if not content:
        return fail("content is required.", code="VALIDATION_ERROR")

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        comment = frappe.new_doc("AOS Live Stream Comment")
        comment.live_stream = live_id
        comment.user = user
        comment.content = content
        comment.status = ACTIVE_STATUS
        comment.insert(ignore_permissions=True)

        publish_comment(live_id, comment)

        return ok(
            "Comment added.",
            data={
                "comment": _serialize_comment(comment),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Add Comment Failed")
        frappe.db.rollback()
        return fail("Failed to add comment.", code="INTERNAL_ERROR")


# REPLY COMMENT
def reply_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:comment:reply:user:{user}",
        ttl_seconds=60,
        limit=REPLY_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many replies.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    parent_id, err = require_id(kwargs.get("parent_comment"), "parent_comment")
    if err:
        return err

    content = (kwargs.get("content") or "").strip()
    if not content:
        return fail("content is required.", code="VALIDATION_ERROR")

    try:
        live, err = validate_live_exists(live_id)
        if err:
            return err

        err = validate_live_active(live)
        if err:
            return err

        parent, err = validate_comment_exists(parent_id)
        if err:
            return err

        err = validate_comment_belongs_to_live(parent, live_id)
        if err:
            return err

        err = validate_comment_active(parent)
        if err:
            return err

        comment = frappe.new_doc("AOS Live Stream Comment")
        comment.live_stream = live_id
        comment.user = user
        comment.content = content
        comment.status = ACTIVE_STATUS
        comment.parent_comment = parent_id
        comment.insert(ignore_permissions=True)

        publish_comment(live_id, comment)

        return ok(
            "Reply added.",
            data={
                "comment": _serialize_comment(comment),
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Reply Comment Failed")
        frappe.db.rollback()
        return fail("Failed to reply.", code="INTERNAL_ERROR")


# LIST COMMENTS
def list_comments_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:comment:list:ip:{ip}",
        ttl_seconds=60,
        limit=LIST_COMMENTS_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    live_id, err = require_id(kwargs.get("live_id"), "live_id")
    if err:
        return err

    try:
        start, limit = _parse_pagination(
            kwargs,
            default_limit=DEFAULT_COMMENTS_LIMIT,
            max_limit=MAX_COMMENTS_LIMIT,
        )

        rows = frappe.get_all(
            "AOS Live Stream Comment",
            filters={
                "live_stream": live_id,
                "parent_comment": ["is", "not set"],
                "status": ACTIVE_STATUS,
            },
            fields=_comment_fields(),
            order_by="creation desc",
            limit_start=start,
            limit_page_length=limit,
        )

        return ok(
            "Comments fetched.",
            data={
                "items": _serialize_comments(rows),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(rows),
                    "has_more": len(rows) == limit,
                },
            },
        )

    except ValueError:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Comments Failed")
        return fail("Failed to fetch comments.", code="INTERNAL_ERROR")


# LIST REPLIES
def list_replies_impl(**kwargs):
    ip = request_ip()

    rl = rate_limit(
        key=f"aos:live:comment:replies:ip:{ip}",
        ttl_seconds=60,
        limit=LIST_REPLIES_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests.",
    )
    if rl:
        return rl

    parent_id, err = require_id(kwargs.get("parent_comment"), "parent_comment")
    if err:
        return err

    try:
        parent, err = validate_comment_exists(parent_id)
        if err:
            return err

        err = validate_comment_active(parent)
        if err:
            return err

        start, limit = _parse_pagination(
            kwargs,
            default_limit=DEFAULT_REPLIES_LIMIT,
            max_limit=MAX_REPLIES_LIMIT,
        )

        rows = frappe.get_all(
            "AOS Live Stream Comment",
            filters={
                "parent_comment": parent_id,
                "status": ACTIVE_STATUS,
            },
            fields=_comment_fields(),
            order_by="creation asc",
            limit_start=start,
            limit_page_length=limit,
        )

        return ok(
            "Replies fetched.",
            data={
                "items": _serialize_comments(rows),
                "pagination": {
                    "start": start,
                    "limit": limit,
                    "count": len(rows),
                    "has_more": len(rows) == limit,
                },
            },
        )

    except ValueError:
        return fail("Invalid pagination values.", code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "List Replies Failed")
        return fail("Failed to fetch replies.", code="INTERNAL_ERROR")


# DELETE COMMENT
def delete_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:live:comment:delete:user:{user}",
        ttl_seconds=60,
        limit=DELETE_COMMENT_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests.",
    )
    if rl:
        return rl

    comment_id, err = require_id(kwargs.get("comment_id"), "comment_id")
    if err:
        return err

    try:
        comment, err = validate_comment_exists(comment_id)
        if err:
            return err

        err = validate_user_can_delete_comment(comment, user)
        if err:
            return err

        if comment.status == DELETED_STATUS:
            return ok(
                "Comment already deleted.",
                data={"comment_id": comment_id},
            )

        if not comment.parent_comment:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream Comment`
                SET status = %s
                WHERE name = %s
                   OR root_comment = %s
                """,
                (DELETED_STATUS, comment.name, comment.name),
            )
        else:
            frappe.db.set_value(
                "AOS Live Stream Comment",
                comment_id,
                {"status": DELETED_STATUS},
                update_modified=False,
            )

        LiveAnalyticsService.sync_comment_count(live_id=comment.live_stream)

        publish_comment_deleted(comment.live_stream, comment_id)

        return ok(
            "Comment deleted.",
            data={"comment_id": comment_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Delete Comment Failed")
        frappe.db.rollback()
        return fail("Failed to delete comment.", code="INTERNAL_ERROR")
