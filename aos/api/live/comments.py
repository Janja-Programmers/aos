"""
Live Comment APIs (implementation).

Handles:
- add_comment
- reply_comment
- list_comments
- list_replies
- delete_comment
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

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
)

from .realtime import publish_comment


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
        comment.seller = live.seller if user == live.seller else None
        comment.content = content
        comment.insert(ignore_permissions=True)

        # REALTIME
        publish_comment(live_id, comment)

        return ok(
            "Comment added.",
            data={"comment_id": comment.name},
        )

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

        comment = frappe.new_doc("AOS Live Stream Comment")
        comment.live_stream = live_id
        comment.user = user
        comment.seller = live.seller if user == live.seller else None
        comment.content = content
        comment.parent_comment = parent_id
        comment.insert(ignore_permissions=True)

        # REALTIME
        publish_comment(live_id, comment)

        return ok(
            "Reply added.",
            data={"comment_id": comment.name},
        )

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

    live_id = kwargs.get("live_id")
    if not live_id:
        return fail("live_id is required.", code="VALIDATION_ERROR")

    try:
        rows = frappe.get_all(
            "AOS Live Stream Comment",
            filters={
                "live_stream": live_id,
                "parent_comment": ["is", "not set"],
                "status": "active",
            },
            fields=[
                "name",
                "user",
                "seller",
                "content",
                "reply_count",
                "creation",
            ],
            order_by="creation desc",
            limit_page_length=20,
        )

        return ok("Comments fetched.", data={"items": rows})

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

    parent_id = kwargs.get("parent_comment")
    if not parent_id:
        return fail("parent_comment is required.", code="VALIDATION_ERROR")

    try:
        rows = frappe.get_all(
            "AOS Live Stream Comment",
            filters={
                "parent_comment": parent_id,
                "status": "active",
            },
            fields=[
                "name",
                "user",
                "seller",
                "content",
                "creation",
            ],
            order_by="creation asc",
            limit_page_length=50,
        )

        return ok("Replies fetched.", data={"items": rows})

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

        live = frappe.db.get_value(
            "AOS Live Stream",
            comment.live_stream,
            ["seller"],
            as_dict=True,
        )

        # Permission check
        if user != comment.user and user != live.seller:
            return fail("Not allowed.", code="PERMISSION_DENIED")

        # CASCADE DELETE
        if not comment.parent_comment:
            # delete parent + all descendants
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream Comment`
                SET status = 'deleted'
                WHERE name = %s
                   OR root_comment = %s
                """,
                (comment.name, comment.name),
            )
        else:
            # delete only this reply
            frappe.db.set_value(
                "AOS Live Stream Comment",
                comment_id,
                {"status": "deleted"},
                update_modified=False,
            )

        return ok(
            "Comment deleted.",
            data={"comment_id": comment_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "Delete Comment Failed")
        frappe.db.rollback()
        return fail("Failed to delete comment.", code="INTERNAL_ERROR")
