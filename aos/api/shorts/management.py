"""
Management APIs for Shorts.

Handles:
- get short detail
- list my shorts
- delete short (soft)
- retry processing
"""

from __future__ import annotations

import frappe

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import validate_limit

from aos.api.shorts.constants import (
    MY_SHORTS_DEFAULT_LIMIT,
    MY_SHORTS_MAX_LIMIT,
)

from aos.api.shorts.utils import (
    build_cursor_where_clause,
    build_time_id_cursor,
    serialize_short_row,
)


# GET SHORT
def get_short_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:get:ip:{request_ip()}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        # Fetch doc first (for access control)
        doc = frappe.get_doc("AOS Short", short_id)

        user = frappe.session.user if frappe.session.user != "Guest" else None

        # ACCESS CONTROL (visibility-based)
        if doc.visibility_status != "visible":
            if not user or doc.owner != user:
                return fail("Short not available.", code="NOT_FOUND")

        rows = frappe.db.sql(
            """
            SELECT
                s.name,
                s.status,
                s.visibility_status,
                s.caption,
                s.hashtags,
                s.playback_url,
                s.thumbnail_url,
                s.duration_seconds,
                s.view_count,
                s.like_count,
                s.comment_count,
                s.share_count,
                s.impression_count,
                s.ranking_score,
                s.posted_on,
                s.seller,
                s.ad,

                sel.shop_name,
                sel.avatar AS seller_avatar,

                ad.title AS ad_title,
                ad.price AS ad_price,
                ad.currency AS ad_currency

            FROM `tabAOS Short` s
            LEFT JOIN `tabAOS Seller` sel ON sel.name = s.seller
            LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad

            WHERE s.name = %s
            """,
            (short_id,),
            as_dict=True,
        )

        if not rows:
            return fail("Short not found.", code="NOT_FOUND")

        return ok(
            "Short fetched.",
            data={"item": serialize_short_row(rows[0])},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "get_short failed")
        return fail("Failed to fetch short", code="INTERNAL_ERROR")


# MY SHORTS
def my_shorts_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:my:user:{user}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    limit = validate_limit(
        kwargs.get("limit"),
        MY_SHORTS_DEFAULT_LIMIT,
        MY_SHORTS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")

    try:
        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            SELECT
                s.name,
                s.status,
                s.caption,
                s.hashtags,
                s.playback_url,
                s.thumbnail_url,
                s.duration_seconds,
                s.view_count,
                s.like_count,
                s.comment_count,
                s.share_count,
                s.impression_count,
                s.ranking_score,
                s.posted_on,
                s.creation,
                s.seller,
                s.ad,

                sel.shop_name,
                sel.avatar AS seller_avatar,

                ad.title AS ad_title,
                ad.price AS ad_price,
                ad.currency AS ad_currency

            FROM `tabAOS Short` s
            LEFT JOIN `tabAOS Seller` sel ON sel.name = s.seller
            LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad

            WHERE
                s.owner = %s
                {where_cursor}

            ORDER BY
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (user, *params_cursor, limit),
            as_dict=True,
        )

        if not rows:
            return ok("My shorts fetched.", data={"items": [], "next_cursor": None})

        items = [serialize_short_row(r) for r in rows]

        last = rows[-1]
        next_cursor = build_time_id_cursor(
            created_on=last["creation"],
            name=last["name"],
        )

        return ok(
            "My shorts fetched.",
            data={"items": items, "next_cursor": next_cursor},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "my_shorts failed")
        return fail("Failed to fetch my shorts", code="INTERNAL_ERROR")


# DELETE SHORT (SOFT)
def delete_short_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        doc = frappe.get_doc("AOS Short", short_id)

        if doc.owner != user:
            return fail("Not allowed.", code="FORBIDDEN")

        doc.visibility_status = "deleted"
        doc.status = "deleted"
        doc.save(ignore_permissions=True)

        return ok(
            "Short deleted.",
            data={"short_id": short_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "delete_short failed")
        frappe.db.rollback()
        return fail("Failed to delete short", code="INTERNAL_ERROR")


# RETRY PROCESSING
def retry_processing_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        doc = frappe.get_doc("AOS Short", short_id)

        if doc.owner != user:
            return fail("Not allowed.", code="FORBIDDEN")

        if doc.status != "failed":
            return fail(
                "Only failed shorts can be retried.",
                code="VALIDATION_ERROR",
            )

        # Reset state
        doc.status = "uploaded"
        doc.save(ignore_permissions=True)
        frappe.db.commit()

        # enqueue via tasks layer
        frappe.enqueue(
            "aos.api.shorts.tasks.process_short_task",
            short_id=doc.name,
            queue="long",
            timeout=1800,
        )

        return ok(
            "Processing restarted.",
            data={"short_id": short_id},
        )

    except Exception:
        frappe.log_error(frappe.get_traceback(), "retry_processing failed")
        frappe.db.rollback()
        return fail("Failed to retry processing", code="INTERNAL_ERROR")
