"""Library/action APIs for Shorts.

Handles:
- save / unsave short
- saved shorts list
- liked shorts list
- controlled downloads
"""

from __future__ import annotations
from typing import Any

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id
from aos.api.shared.formatters import humanize_count
from aos.services.minio_service import MinioService

from aos.api.shorts.constants import (
    SAVE_TOGGLE_RATE_LIMIT_PER_MINUTE,
    REPOST_TOGGLE_RATE_LIMIT_PER_MINUTE,
    SAVED_SHORTS_DEFAULT_LIMIT,
    SAVED_SHORTS_MAX_LIMIT,
    LIKED_SHORTS_DEFAULT_LIMIT,
    LIKED_SHORTS_MAX_LIMIT,
    REPOSTED_SHORTS_DEFAULT_LIMIT,
    REPOSTED_SHORTS_MAX_LIMIT,
    DOWNLOAD_SHORT_LIMIT_PER_MINUTE_PER_IP,
)
from aos.api.shorts.validators import validate_limit
from aos.api.shorts.utils import build_cursor_where_clause, build_time_id_cursor
from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.activity import (
    hide_short_repost_activity,
    record_short_repost_activity,
)
from aos.api.shorts.management import (
    _get_optional_viewer,
    _select_short_rows_sql,
    _serialize_rows_with_viewer_state,
)

RANKING_TASK = "aos.api.shorts.tasks.update_short_score_task"


def _select_short_rows_with_action_sql(action_alias: str) -> str:
    """Add action creation/name fields to the common short SELECT."""
    base_sql = _select_short_rows_sql()
    marker = "\n        FROM `tabAOS Short` s"
    action_fields = (
        ",\n"
        f"            {action_alias}.creation AS action_creation,\n"
        f"            {action_alias}.name AS action_name\n"
    )

    return base_sql.replace(marker, action_fields + marker, 1)


def _filter_viewable_rows(
    rows: list[dict[str, Any]],
    *,
    viewer: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    safe_rows = []

    for row in rows or []:
        if can_view_short(row, current_user=viewer):
            safe_rows.append(row)
            if len(safe_rows) >= limit + 1:
                break

    return safe_rows


def _build_collection_response(
    *,
    rows: list[dict[str, Any]],
    limit: int,
    viewer: str,
    message: str,
):
    safe_rows = _filter_viewable_rows(rows, viewer=viewer, limit=limit)

    if not safe_rows:
        return ok(
            message,
            data={"items": [], "next_cursor": None, "has_more": False},
        )

    has_more = len(safe_rows) > limit
    visible_rows = safe_rows[:limit]
    items = _serialize_rows_with_viewer_state(visible_rows, viewer=viewer)

    next_cursor = None
    if has_more:
        last = visible_rows[-1]
        next_cursor = build_time_id_cursor(
            created_on=last["action_creation"],
            name=last["action_name"],
        )

    return ok(
        message,
        data={"items": items, "next_cursor": next_cursor, "has_more": has_more},
    )


def toggle_save_short_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:save:user:{user}",
        ttl_seconds=60,
        limit=SAVE_TOGGLE_RATE_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        short = frappe.db.get_value(
            "AOS Short",
            short_id,
            ["name", "owner", "status", "visibility_status", "audience"],
            as_dict=True,
        )

        if not short:
            return fail("Short not found.", code="NOT_FOUND")

        if short.status != "ready" or short.visibility_status != "visible":
            return fail("Short is not available for saving.", code="VALIDATION_ERROR")

        if not can_view_short(short, current_user=user):
            return fail("Short not found.", code="NOT_FOUND")

        existing = frappe.get_all(
            "AOS Short Save",
            filters={"short": short_id, "user": user},
            fields=["name"],
            limit=1,
        )

        if not existing:
            frappe.get_doc(
                {
                    "doctype": "AOS Short Save",
                    "short": short_id,
                    "user": user,
                }
            ).insert(ignore_permissions=True)

            saved = True
            message = "Saved."
        else:
            frappe.delete_doc(
                "AOS Short Save",
                existing[0].name,
                ignore_permissions=True,
            )
            saved = False
            message = "Removed from saved shorts."

        frappe.db.commit()

        save_count = frappe.db.get_value("AOS Short", short_id, "save_count") or 0

        frappe.enqueue(RANKING_TASK, short_id=short_id, queue="short")

        return ok(
            message,
            data={
                "short_id": short_id,
                "saved": saved,
                "viewer_state": {"is_saved": saved},
                "metrics": {
                    "save_count": int(save_count),
                    "save_count_display": humanize_count(save_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "toggle_save_short failed")
        frappe.db.rollback()
        return fail("Failed to toggle save", code="INTERNAL_ERROR")


def saved_shorts_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    limit = validate_limit(
        kwargs.get("limit"),
        SAVED_SHORTS_DEFAULT_LIMIT,
        SAVED_SHORTS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")

    where_cursor, params_cursor = build_cursor_where_clause(
        created_field="sv.creation",
        name_field="sv.name",
        cursor=cursor,
    )

    try:
        rows = frappe.db.sql(
            f"""
            {_select_short_rows_with_action_sql("sv")}
            INNER JOIN `tabAOS Short Save` sv ON sv.short = s.name

            WHERE
                sv.user = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                sv.creation DESC,
                sv.name DESC

            LIMIT %s
            """,
            (user, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_collection_response(
            rows=rows,
            limit=limit,
            viewer=user,
            message="Saved shorts fetched.",
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "saved_shorts failed")
        return fail("Failed to fetch saved shorts", code="INTERNAL_ERROR")


def liked_shorts_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    limit = validate_limit(
        kwargs.get("limit"),
        LIKED_SHORTS_DEFAULT_LIMIT,
        LIKED_SHORTS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")

    where_cursor, params_cursor = build_cursor_where_clause(
        created_field="lk.creation",
        name_field="lk.name",
        cursor=cursor,
    )

    try:
        rows = frappe.db.sql(
            f"""
            {_select_short_rows_with_action_sql("lk")}
            INNER JOIN `tabAOS Short Like` lk ON lk.short = s.name

            WHERE
                lk.user = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                lk.creation DESC,
                lk.name DESC

            LIMIT %s
            """,
            (user, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_collection_response(
            rows=rows,
            limit=limit,
            viewer=user,
            message="Liked shorts fetched.",
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "liked_shorts failed")
        return fail("Failed to fetch liked shorts", code="INTERNAL_ERROR")


def download_short_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:download:ip:{request_ip()}",
        ttl_seconds=60,
        limit=DOWNLOAD_SHORT_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        viewer = _get_optional_viewer()
        session_id = str(kwargs.get("session_id") or "").strip() or None

        if not viewer and not session_id:
            return fail(
                "session_id is required for guest downloads.",
                code="VALIDATION_ERROR",
            )

        short = frappe.db.get_value(
            "AOS Short",
            short_id,
            [
                "name",
                "owner",
                "status",
                "visibility_status",
                "audience",
                "allow_downloads",
                "processed_file_key",
            ],
            as_dict=True,
        )

        if not short:
            return fail("Short not found.", code="NOT_FOUND")

        is_owner = bool(viewer and viewer == short.owner)

        if short.status != "ready":
            return fail("Short is not ready for download.", code="VALIDATION_ERROR")

        if not is_owner:
            if short.visibility_status != "visible":
                return fail("Short not found.", code="NOT_FOUND")

            if not can_view_short(short, current_user=viewer):
                return fail("Short not found.", code="NOT_FOUND")

            if not int(short.allow_downloads or 0):
                return fail("Downloads are disabled for this short.", code="FORBIDDEN")

        download_file_key = short.processed_file_key

        if not download_file_key:
            return fail("Download file is not available.", code="NOT_FOUND")

        service = MinioService()
        if not service.file_exists(download_file_key):
            return fail("Download file is missing.", code="NOT_FOUND")

        expiry_minutes = 15
        download_url = service.get_presigned_download_url(
            download_file_key,
            expiry_minutes=expiry_minutes,
        )

        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET download_count = download_count + 1
            WHERE name = %s
            """,
            (short_id,),
        )

        event_user = viewer
        if event_user or session_id:
            frappe.get_doc(
                {
                    "doctype": "AOS Short Event",
                    "short": short_id,
                    "user": event_user,
                    "session_id": None if event_user else session_id,
                    "event_type": "download",
                }
            ).insert(ignore_permissions=True)

        frappe.db.commit()

        download_count = frappe.db.get_value("AOS Short", short_id, "download_count") or 0

        return ok(
            "Download URL generated.",
            data={
                "short_id": short_id,
                "download_url": download_url,
                "download_file_key": download_file_key,
                "expires_in_seconds": expiry_minutes * 60,
                "metrics": {
                    "download_count": int(download_count),
                    "download_count_display": humanize_count(download_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "download_short failed")
        frappe.db.rollback()
        return fail("Failed to generate download URL", code="INTERNAL_ERROR")



def toggle_repost_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:repost:user:{user}",
        ttl_seconds=60,
        limit=REPOST_TOGGLE_RATE_LIMIT_PER_MINUTE,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    note = (kwargs.get("note") or "").strip() or None

    try:
        existing = frappe.get_all(
            "AOS Short Repost",
            filters={"short": short_id, "user": user, "status": "active"},
            fields=["name"],
            limit=1,
        )

        # Removing a repost should remain possible even if the original short
        # was later hidden, made private, deleted, or otherwise unavailable.
        if existing:
            repost_doc = frappe.get_doc("AOS Short Repost", existing[0].name)
            repost_doc.status = "deleted"
            repost_doc.save(ignore_permissions=True)
            hide_short_repost_activity(user=user, short_id=short_id)
            reposted = False
            message = "Repost removed."
        else:
            short = frappe.db.get_value(
                "AOS Short",
                short_id,
                ["name", "owner", "status", "visibility_status", "audience"],
                as_dict=True,
            )

            if not short:
                return fail("Short not found.", code="NOT_FOUND")

            if short.owner == user:
                return fail("You cannot repost your own short.", code="VALIDATION_ERROR")

            if short.status != "ready" or short.visibility_status != "visible":
                return fail(
                    "Short is not available for reposting.",
                    code="VALIDATION_ERROR",
                )

            if not can_view_short(short, current_user=user):
                return fail("Short not found.", code="NOT_FOUND")

            frappe.get_doc(
                {
                    "doctype": "AOS Short Repost",
                    "short": short_id,
                    "user": user,
                    "note": note,
                    "status": "active",
                }
            ).insert(ignore_permissions=True)
            record_short_repost_activity(user=user, short_id=short_id)
            reposted = True
            message = "Reposted."

        frappe.db.commit()

        repost_count = frappe.db.get_value("AOS Short", short_id, "repost_count") or 0
        share_count = frappe.db.get_value("AOS Short", short_id, "share_count") or 0

        frappe.enqueue(RANKING_TASK, short_id=short_id, queue="short")

        return ok(
            message,
            data={
                "short_id": short_id,
                "reposted": reposted,
                "viewer_state": {"is_reposted": reposted},
                "metrics": {
                    "repost_count": int(repost_count),
                    "repost_count_display": humanize_count(repost_count),
                    "share_count": int(share_count),
                    "share_count_display": humanize_count(share_count),
                },
            },
        )

    except frappe.ValidationError as ex:
        frappe.db.rollback()
        return fail(str(ex), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "toggle_repost failed")
        frappe.db.rollback()
        return fail("Failed to toggle repost", code="INTERNAL_ERROR")


def reposted_shorts_impl(**kwargs):
    viewer = _get_optional_viewer()
    target_user = kwargs.get("user") or kwargs.get("target_user")

    if not target_user:
        user, err = require_login()
        if err:
            return err
        target_user = user

    if not frappe.db.exists("User", target_user):
        return fail("User not found.", code="NOT_FOUND")

    limit = validate_limit(
        kwargs.get("limit"),
        REPOSTED_SHORTS_DEFAULT_LIMIT,
        REPOSTED_SHORTS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")

    where_cursor, params_cursor = build_cursor_where_clause(
        created_field="rp.creation",
        name_field="rp.name",
        cursor=cursor,
    )

    try:
        rows = frappe.db.sql(
            f"""
            {_select_short_rows_with_action_sql("rp")}
            INNER JOIN `tabAOS Short Repost` rp ON rp.short = s.name

            WHERE
                rp.user = %s
                AND rp.status = 'active'
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {where_cursor}

            ORDER BY
                rp.creation DESC,
                rp.name DESC

            LIMIT %s
            """,
            (target_user, *params_cursor, limit + 1),
            as_dict=True,
        )

        # Reposted shorts are only returned if the current viewer can view the
        # original short. This keeps repost tabs privacy-safe.
        safe_rows = _filter_viewable_rows(rows, viewer=viewer, limit=limit)

        if not safe_rows:
            return ok(
                "Reposted shorts fetched.",
                data={"items": [], "next_cursor": None, "has_more": False},
            )

        has_more = len(safe_rows) > limit
        visible_rows = safe_rows[:limit]
        items = _serialize_rows_with_viewer_state(visible_rows, viewer=viewer)

        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(
                created_on=last["action_creation"],
                name=last["action_name"],
            )

        return ok(
            "Reposted shorts fetched.",
            data={"items": items, "next_cursor": next_cursor, "has_more": has_more},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "reposted_shorts failed")
        return fail("Failed to fetch reposted shorts", code="INTERNAL_ERROR")
