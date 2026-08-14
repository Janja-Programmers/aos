"""
Comments APIs for Shorts.

Handles:
- add comment
- reply
- list comments
- list replies
- delete (soft)
- like / unlike comment
"""

from __future__ import annotations
from typing import Any

import frappe
from frappe.utils import cint

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.public_errors import safe_fail_from_exception
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shared.formatters import humanize_count
from aos.api.shared.validators import require_id

from aos.services.notification_service import NotificationService

from aos.api.shorts.validators import (
    validate_comment_text,
    validate_limit,
)

from aos.api.shorts.constants import (
    COMMENT_DEFAULT_LIMIT,
    COMMENT_MAX_LIMIT,
    REPLY_DEFAULT_LIMIT,
    REPLY_MAX_LIMIT,
    COMMENT_LIMIT_PER_MINUTE_PER_USER,
    LIKE_TOGGLE_RATE_LIMIT_PER_MINUTE,
)

from aos.api.shorts.utils import (
    build_cursor_where_clause,
    build_time_id_cursor,
    serialize_comment_row,
)

from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.activity import (
    hide_short_comment_activity,
    record_short_comment_activity,
)
from aos.api.shorts.mentions import (
    delete_comment_mentions,
    get_comment_mentions_map,
    sync_comment_mentions,
)

RANKING_TASK = "aos.api.shorts.tasks.update_short_score_task"


# COMMON
def _get_optional_viewer() -> str | None:
    user = current_user()
    if not user or user == "Guest":
        return None

    return user


def _get_short_owner(short_id: str) -> str | None:
    if not short_id:
        return None

    return frappe.db.get_value("AOS Short", short_id, "owner")


def _ensure_commentable_short(
    short_id: str,
    *,
    viewer: str | None = None,
    require_comments_allowed: bool = False,
):
    """
    Ensure short is available for comment-related actions.

    Rules:
    - Short must exist.
    - Non-owners can only access ready + visible shorts.
    - Audience privacy must be respected.
    - If require_comments_allowed=True, short.allow_comments must be enabled.

    Note:
    Listing existing comments/replies should not require allow_comments = 1.
    Disabled comments only block new comments and new replies.
    """
    short = frappe.db.get_value(
        "AOS Short",
        short_id,
        [
            "name",
            "status",
            "visibility_status",
            "owner",
            "audience",
            "allow_comments",
        ],
        as_dict=True,
    )

    if not short:
        return None, fail("Short not found.", error="NOT_FOUND")

    is_owner = bool(viewer and short.owner == viewer)

    if not is_owner:
        if short.status != "ready" or short.visibility_status != "visible":
            return None, fail("Short not available.", error="NOT_FOUND")

        if not can_view_short(short, current_user=viewer):
            return None, fail("Short not available.", error="NOT_FOUND")

    if require_comments_allowed and not cint(short.allow_comments):
        return None, fail(
            "Comments are disabled for this short.",
            error="COMMENTS_DISABLED",
        )

    return short, None


def _ensure_active_comment(
    comment_id: str,
    *,
    viewer: str | None = None,
    require_comments_allowed: bool = False,
):
    """
    Ensure comment exists, is active, and belongs to an available short.
    """
    comment = frappe.db.get_value(
        "AOS Short Comment",
        comment_id,
        ["name", "short", "user", "status"],
        as_dict=True,
    )

    if not comment:
        return None, None, fail("Comment not found.", error="NOT_FOUND")

    if comment.status != "active":
        return None, None, fail("Comment not available.", error="NOT_FOUND")

    short, err = _ensure_commentable_short(
        comment.short,
        viewer=viewer,
        require_comments_allowed=require_comments_allowed,
    )
    if err:
        return None, None, err

    return comment, short, None


def _load_liked_comment_ids(
    viewer: str | None,
    comment_ids: list[str],
) -> set[str]:
    """
    Batch-load comments liked by the current viewer.
    """
    if not viewer or not comment_ids:
        return set()

    rows = frappe.get_all(
        "AOS Short Comment Like",
        filters={
            "user": viewer,
            "comment": ["in", comment_ids],
        },
        pluck="comment",
    )

    return set(rows or [])


def _build_comment_viewer_state(
    row: dict[str, Any],
    *,
    viewer: str | None,
    short_owner: str | None,
    liked_comment_ids: set[str],
) -> dict[str, bool]:
    comment_id = row.get("name")
    comment_user = row.get("user")

    is_logged_in = bool(viewer)
    is_owner = bool(viewer and comment_user and viewer == comment_user)
    is_short_owner = bool(viewer and short_owner and viewer == short_owner)

    can_delete = bool(is_owner or is_short_owner)

    return {
        "is_liked": bool(comment_id and comment_id in liked_comment_ids),
        "is_owner": is_owner,
        "can_delete": can_delete,
        "can_report": bool(is_logged_in and not is_owner),
    }


def _serialize_comments_with_viewer_state(
    rows: list[dict[str, Any]],
    *,
    viewer: str | None,
    short_owner: str | None,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    comment_ids = [
        row.get("name")
        for row in rows
        if row.get("name")
    ]

    liked_comment_ids = _load_liked_comment_ids(viewer, comment_ids)
    mention_map = get_comment_mentions_map(comment_ids)

    for row in rows:
        row["mentions"] = mention_map.get(row.get("name"), [])

    return [
        serialize_comment_row(
            row,
            viewer_state=_build_comment_viewer_state(
                row,
                viewer=viewer,
                short_owner=short_owner,
                liked_comment_ids=liked_comment_ids,
            ),
        )
        for row in rows
    ]


def _fetch_comment_row(comment_id: str) -> dict[str, Any] | None:
    rows = frappe.db.sql(
        """
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
        WHERE c.name = %s
        LIMIT 1
        """,
        (comment_id,),
        as_dict=True,
    )

    return rows[0] if rows else None


def _get_comment_like_count(comment_id: str) -> int:
    return int(
        frappe.db.get_value(
            "AOS Short Comment",
            comment_id,
            "like_count",
        )
        or 0
    )


# ADD COMMENT (TOP LEVEL)
def add_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:comment:user:{user}",
        ttl_seconds=60,
        limit=COMMENT_LIMIT_PER_MINUTE_PER_USER,
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
        short, err = _ensure_commentable_short(
            short_id,
            viewer=user,
            require_comments_allowed=True,
        )
        if err:
            return err

        doc = frappe.get_doc(
            {
                "doctype": "AOS Short Comment",
                "short": short_id,
                "user": user,
                "comment": comment,
                "status": "active",
            }
        )
        doc.insert(ignore_permissions=True)

        sync_comment_mentions(
            short_id=short_id,
            comment_id=doc.name,
            text=comment,
            mentioned_by=user,
            is_reply=False,
        )

        record_short_comment_activity(
            user=user,
            short_id=short_id,
            comment_id=doc.name,
            comment_text=comment,
        )

        # Notify short creator/poster, not seller.
        if short.owner and short.owner != user:
            NotificationService.notify_short_comment(
                user=short.owner,
                actor=user,
                short_id=short_id,
                content=comment,
                event_identity=doc.name,
            )


        frappe.enqueue(
            RANKING_TASK,
            short_id=short_id,
            queue="short",
            enqueue_after_commit=True,
        )

        row = _fetch_comment_row(doc.name)
        item = None

        if row:
            item = _serialize_comments_with_viewer_state(
                [row],
                viewer=user,
                short_owner=short.owner,
            )[0]

        return ok(
            "Comment added.",
            data={
                "comment_id": doc.name,
                "item": item,
            },
        )

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Shorts operation failed.", "add_comment failed")
        return fail("Failed to add comment", error="INTERNAL_ERROR")


# REPLY COMMENT
def reply_comment_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:reply:user:{user}",
        ttl_seconds=60,
        limit=COMMENT_LIMIT_PER_MINUTE_PER_USER,
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

        if parent.status != "active":
            return fail("Comment not available.", error="NOT_FOUND")

        short, err = _ensure_commentable_short(
            parent.short,
            viewer=user,
            require_comments_allowed=True,
        )
        if err:
            return err

        doc = frappe.get_doc(
            {
                "doctype": "AOS Short Comment",
                "short": parent.short,
                "user": user,
                "comment": comment,
                "parent_comment": parent.name,
                "root_comment": parent.root_comment or parent.name,
                "status": "active",
            }
        )
        doc.insert(ignore_permissions=True)

        sync_comment_mentions(
            short_id=parent.short,
            comment_id=doc.name,
            text=comment,
            mentioned_by=user,
            is_reply=True,
        )

        record_short_comment_activity(
            user=user,
            short_id=parent.short,
            comment_id=doc.name,
            comment_text=comment,
            parent_comment_id=parent.name,
        )

        # Notify comment owner.
        if parent.user and parent.user != user:
            NotificationService.notify_comment_reply(
                user=parent.user,
                actor=user,
                short_id=parent.short,
                comment_id=parent.name,
                content=comment,
                event_identity=doc.name,
            )


        frappe.enqueue(
            RANKING_TASK,
            short_id=parent.short,
            queue="short",
            enqueue_after_commit=True,
        )

        row = _fetch_comment_row(doc.name)
        item = None

        if row:
            item = _serialize_comments_with_viewer_state(
                [row],
                viewer=user,
                short_owner=short.owner,
            )[0]

        return ok(
            "Reply added.",
            data={
                "comment_id": doc.name,
                "item": item,
            },
        )

    except frappe.DoesNotExistError:
        return fail("Comment not found.", error="NOT_FOUND")

    except frappe.ValidationError as ex:
        return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

    except Exception:
        frappe.log_error("Shorts operation failed.", "reply_comment failed")
        return fail("Failed to reply", error="INTERNAL_ERROR")


# TOGGLE COMMENT LIKE
def toggle_comment_like_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:comment_like:user:{user}",
        ttl_seconds=60,
        limit=LIKE_TOGGLE_RATE_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    comment_id, err = require_id(kwargs.get("comment_id"), "comment_id")
    if err:
        return err

    try:
        comment, short, err = _ensure_active_comment(
            comment_id,
            viewer=user,
            require_comments_allowed=False,
        )
        if err:
            return err

        existing = frappe.get_all(
            "AOS Short Comment Like",
            filters={
                "comment": comment_id,
                "user": user,
            },
            fields=["name"],
            limit=1,
        )

        if not existing:
            frappe.get_doc(
                {
                    "doctype": "AOS Short Comment Like",
                    "comment": comment_id,
                    "user": user,
                }
            ).insert(ignore_permissions=True)

            liked = True
            message = "Comment liked."

        else:
            frappe.delete_doc(
                "AOS Short Comment Like",
                existing[0].name,
                ignore_permissions=True,
            )

            liked = False
            message = "Comment unliked."


        like_count = _get_comment_like_count(comment_id)

        frappe.enqueue(
            RANKING_TASK,
            short_id=comment.short,
            queue="short",
            enqueue_after_commit=True,
        )

        return ok(
            message,
            data={
                "comment_id": comment_id,

                # Backward-compatible convenience field.
                # Frontend should prefer viewer_state.is_liked.
                "liked": liked,

                "viewer_state": {
                    "is_liked": liked,
                },
                "metrics": {
                    "like_count": like_count,
                    "like_count_display": humanize_count(like_count),
                },
            },
        )

    except Exception as ex:

        if is_duplicate_entry_error(ex):
            like_count = _get_comment_like_count(comment_id)

            return ok(
                "Comment already liked.",
                data={
                    "comment_id": comment_id,
                    "liked": True,
                    "viewer_state": {
                        "is_liked": True,
                    },
                    "metrics": {
                        "like_count": like_count,
                        "like_count_display": humanize_count(like_count),
                    },
                },
            )

        if isinstance(ex, frappe.ValidationError):
            return safe_fail_from_exception(ex, fallback="Invalid request.", error="VALIDATION_ERROR")

        frappe.log_error("Shorts operation failed.", "toggle_comment_like failed")
        return fail("Failed to toggle comment like", error="INTERNAL_ERROR")


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
        viewer = _get_optional_viewer()

        short, err = _ensure_commentable_short(
            short_id,
            viewer=viewer,
            require_comments_allowed=False,
        )
        if err:
            return err

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
            (short_id, *params_cursor, limit + 1),
            as_dict=True,
        )

        if not rows:
            return ok(
                "Comments fetched.",
                data={
                    "items": [],
                    "next_cursor": None,
                    "has_more": False,
                },
            )

        has_more = len(rows) > limit
        visible_rows = rows[:limit]

        items = _serialize_comments_with_viewer_state(
            visible_rows,
            viewer=viewer,
            short_owner=short.owner,
        )

        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(
                created_on=last["creation"],
                name=last["name"],
            )

        return ok(
            "Comments fetched.",
            data={
                "items": items,
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "list_comments failed")
        return fail("Failed to fetch comments", error="INTERNAL_ERROR")


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
        root = frappe.db.get_value(
            "AOS Short Comment",
            root_comment_id,
            ["name", "short", "status"],
            as_dict=True,
        )

        if not root or root.status != "active":
            return fail("Comment not found.", error="NOT_FOUND")

        viewer = _get_optional_viewer()

        short, err = _ensure_commentable_short(
            root.short,
            viewer=viewer,
            require_comments_allowed=False,
        )
        if err:
            return err

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
            (root_comment_id, *params_cursor, limit + 1),
            as_dict=True,
        )

        if not rows:
            return ok(
                "Replies fetched.",
                data={
                    "items": [],
                    "next_cursor": None,
                    "has_more": False,
                },
            )

        has_more = len(rows) > limit
        visible_rows = rows[:limit]

        items = _serialize_comments_with_viewer_state(
            visible_rows,
            viewer=viewer,
            short_owner=short.owner,
        )

        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(
                created_on=last["creation"],
                name=last["name"],
            )

        return ok(
            "Replies fetched.",
            data={
                "items": items,
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "list_replies failed")
        return fail("Failed to fetch replies", error="INTERNAL_ERROR")


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

        if doc.status != "active":
            return fail("Comment not found.", error="NOT_FOUND")

        short_owner = _get_short_owner(doc.short)

        # Comment owner OR short owner can delete.
        if doc.user != user and short_owner != user:
            return fail("Not allowed.", error="FORBIDDEN")

        short_id = doc.short
        # Serialize comment-count changes with concurrent replies/deletes.
        frappe.db.sql("SELECT name FROM `tabAOS Short` WHERE name = %s FOR UPDATE", (short_id,))
        deleted_count = 1
        if not doc.parent_comment:
            deleted_count = int(frappe.db.sql(
                """SELECT COUNT(*) FROM `tabAOS Short Comment`
                   WHERE root_comment = %s AND status != 'deleted'""",
                (doc.name,),
            )[0][0] or 0)
            deleted_count = max(1, deleted_count)

        # Hide Activity Center comment-history rows for the deleted comment and
        # any cascaded replies before their source records are soft-deleted.
        activity_rows = [
            {"name": doc.name, "user": doc.user},
        ]

        if not doc.parent_comment:
            reply_activity_rows = frappe.get_all(
                "AOS Short Comment",
                filters={
                    "root_comment": doc.name,
                    "status": ["!=", "deleted"],
                },
                fields=["name", "user"],
            )

            seen_comment_ids = {doc.name}
            for row in reply_activity_rows or []:
                if row.name in seen_comment_ids:
                    continue
                seen_comment_ids.add(row.name)
                activity_rows.append({"name": row.name, "user": row.user})

        for row in activity_rows:
            hide_short_comment_activity(
                user=row.get("user"),
                comment_id=row.get("name"),
            )
            delete_comment_mentions(row.get("name"))

        # CASCADE DELETE replies if deleting a top-level comment.
        if not doc.parent_comment:
            frappe.db.sql(
                """
                UPDATE `tabAOS Short Comment`
                SET status = 'deleted'
                WHERE root_comment = %s
                  AND status != 'deleted'
                """,
                (doc.name,),
            )

        # DELETE THIS COMMENT. The controller decrements one; account for
        # replies already cascade-deleted above without invoking their hooks.
        doc.soft_delete()
        if deleted_count > 1:
            frappe.db.sql(
                """UPDATE `tabAOS Short`
                   SET comment_count = GREATEST(COALESCE(comment_count, 0) - %s, 0)
                   WHERE name = %s""",
                (deleted_count - 1, short_id),
            )

        frappe.enqueue(
            RANKING_TASK,
            short_id=short_id,
            queue="short",
            enqueue_after_commit=True,
        )

        return ok(
            "Comment deleted.",
            data={"comment_id": comment_id},
        )

    except frappe.DoesNotExistError:
        return fail("Comment not found.", error="NOT_FOUND")

    except Exception:
        frappe.log_error("Shorts operation failed.", "delete_comment failed")
        return fail("Failed to delete comment", error="INTERNAL_ERROR")
