"""
Comments APIs for Shorts.

Handles:
- add comment
- reply
- list comments
- list replies
- delete (soft)
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import (
    validate_comment_text,
    validate_limit,
)

from aos.api.shorts.constants import (
    COMMENT_DEFAULT_LIMIT,
    COMMENT_MAX_LIMIT,
    REPLY_DEFAULT_LIMIT,
    REPLY_MAX_LIMIT,
)

from aos.api.shorts.utils import (
    build_cursor_where_clause,
    build_time_id_cursor,
    serialize_comment_row,
)

RANKING_TASK = "aos.api.shorts.tasks.update_short_score_task"


# ADD COMMENT (TOP LEVEL)
def add_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:comment:user:{user}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    comment, err = validate_comment_text(kwargs.get("comment"))
    if err:
        return err

    try:
        doc = frappe.get_doc({
            "doctype": "AOS Short Comment",
            "short": short_id,
            "user": user,
            "comment": comment,
            "status": "active",
        })
        doc.insert(ignore_permissions=True)

        frappe.enqueue(
            RANKING_TASK,
            short_id=short_id,
            queue="short",
        )

        return ok(
            "Comment added.",
            data={"comment_id": doc.name},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "add_comment failed")
        frappe.db.rollback()
        return fail("Failed to add comment", code="INTERNAL_ERROR")


# REPLY COMMENT
def reply_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:reply:user:{user}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    parent_comment_id, err = require_id(
        kwargs.get("parent_comment_id"),
        "parent_comment_id",
    )
    if err:
        return err

    comment, err = validate_comment_text(kwargs.get("comment"))
    if err:
        return err

    try:
        parent = frappe.get_doc("AOS Short Comment", parent_comment_id)

        doc = frappe.get_doc({
            "doctype": "AOS Short Comment",
            "short": parent.short,
            "user": user,
            "comment": comment,
            "parent_comment": parent.name,
            "root_comment": parent.root_comment or parent.name,
            "status": "active",
        })
        doc.insert(ignore_permissions=True)

        frappe.enqueue(
            RANKING_TASK,
            short_id=parent.short,
            queue="short",
        )

        return ok(
            "Reply added.",
            data={"comment_id": doc.name},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reply_comment failed")
        frappe.db.rollback()
        return fail("Failed to reply", code="INTERNAL_ERROR")


# LIST COMMENTS (TOP LEVEL)
def list_comments_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:comments:ip:{request_ip()}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    limit = validate_limit(
        kwargs.get("limit"),
        COMMENT_DEFAULT_LIMIT,
        COMMENT_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")

    try:
        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="c.creation",
            name_field="c.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                c.name,
                c.short,
                c.user,
                c.seller,
                c.comment,
                c.parent_comment,
                c.root_comment,
                c.reply_count,
                c.like_count,
                c.status,
                c.creation
            FROM `tabAOS Short Comment` c
            WHERE
                c.short = %s
                AND c.parent_comment IS NULL
                AND c.status = 'active'
                {where_cursor}
            ORDER BY
                c.creation DESC,
                c.name DESC
            LIMIT %s
            """,
            (short_id, *params_cursor, limit),
            as_dict=True,
        )

        if not rows:
            return ok("Comments fetched.", data={"items": [], "next_cursor": None})

        items = [serialize_comment_row(r) for r in rows]

        last = rows[-1]
        next_cursor = build_time_id_cursor(
            created_on=last["creation"],
            name=last["name"],
        )

        return ok(
            "Comments fetched.",
            data={"items": items, "next_cursor": next_cursor},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "list_comments failed")
        return fail("Failed to fetch comments", code="INTERNAL_ERROR")


# LIST REPLIES
def list_replies_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:replies:ip:{request_ip()}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    root_comment_id, err = require_id(
        kwargs.get("root_comment_id"),
        "root_comment_id",
    )
    if err:
        return err

    limit = validate_limit(
        kwargs.get("limit"),
        REPLY_DEFAULT_LIMIT,
        REPLY_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")

    try:
        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="c.creation",
            name_field="c.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                c.name,
                c.short,
                c.user,
                c.seller,
                c.comment,
                c.parent_comment,
                c.root_comment,
                c.reply_count,
                c.like_count,
                c.status,
                c.creation
            FROM `tabAOS Short Comment` c
            WHERE
                c.root_comment = %s
                AND c.parent_comment IS NOT NULL
                AND c.status = 'active'
                {where_cursor}
            ORDER BY
                c.creation ASC,
                c.name ASC
            LIMIT %s
            """,
            (root_comment_id, *params_cursor, limit),
            as_dict=True,
        )

        if not rows:
            return ok("Replies fetched.", data={"items": [], "next_cursor": None})

        items = [serialize_comment_row(r) for r in rows]

        last = rows[-1]
        next_cursor = build_time_id_cursor(
            created_on=last["creation"],
            name=last["name"],
        )

        return ok(
            "Replies fetched.",
            data={"items": items, "next_cursor": next_cursor},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "list_replies failed")
        return fail("Failed to fetch replies", code="INTERNAL_ERROR")


# DELETE COMMENT (SOFT + CASCADE)
def delete_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    comment_id, err = require_id(kwargs.get("comment_id"), "comment_id")
    if err:
        return err

    try:
        doc = frappe.get_doc("AOS Short Comment", comment_id)

        if doc.user != user:
            return fail("Not allowed.", code="FORBIDDEN")

        short_id = doc.short

        # CASCADE DELETE (only for top-level comments)
        if not doc.parent_comment:
            frappe.db.sql(
                """
                UPDATE `tabAOS Short Comment`
                SET status = 'deleted'
                WHERE root_comment = %s
                """,
                (doc.name,),
            )

        # DELETE THIS COMMENT
        doc.soft_delete()

        frappe.enqueue(
            RANKING_TASK,
            short_id=short_id,
            queue="short",
        )

        return ok(
            "Comment deleted.",
            data={"comment_id": comment_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "delete_comment failed")
        frappe.db.rollback()
        return fail("Failed to delete comment", code="INTERNAL_ERROR")
