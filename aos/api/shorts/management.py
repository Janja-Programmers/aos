"""
Management APIs for Shorts.

Handles:
- get short detail
- list my shorts
- list user/profile shorts
- delete short (soft)
- retry processing
"""

from __future__ import annotations
from typing import Any

import frappe

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id

from aos.api.shorts.validators import validate_limit, validate_content_mode

from aos.api.shorts.constants import (
    MY_SHORTS_DEFAULT_LIMIT,
    MY_SHORTS_MAX_LIMIT,
    USER_SHORTS_DEFAULT_LIMIT,
    USER_SHORTS_MAX_LIMIT,
)

from aos.api.shorts.utils import (
    build_cursor_where_clause,
    build_time_id_cursor,
    serialize_short_row,
)

from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.mentions import get_short_mentions_map
from aos.api.shorts.sounds import get_short_sound_map
from aos.services.video_processing_service import create_video_processing_job
from aos.services.media.media_service import MediaService
from aos.services.shorts.repository import ShortsRepository
from aos.services.accounts.identity import public_account_id_for_user, resolve_account_reference
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map as social_relationship_map


# COMMON
def _get_optional_viewer() -> str | None:
    """
    Resolve viewer for public detail/profile endpoints.

    Guest users return None.
    Logged-in users receive real viewer_state.
    """
    user = current_user()
    if not user or user == "Guest":
        return None

    return user


def _load_liked_short_ids(viewer: str | None, short_ids: list[str]) -> set[str]:
    if not viewer or not short_ids:
        return set()

    rows = frappe.get_all(
        "AOS Short Like",
        filters={
            "user": viewer,
            "short": ["in", short_ids],
        },
        pluck="short",
    )

    return set(rows or [])


def _load_saved_short_ids(viewer: str | None, short_ids: list[str]) -> set[str]:
    if not viewer or not short_ids:
        return set()

    rows = frappe.get_all(
        "AOS Short Save",
        filters={
            "user": viewer,
            "short": ["in", short_ids],
        },
        pluck="short",
    )

    return set(rows or [])


def _load_reposted_short_ids(viewer: str | None, short_ids: list[str]) -> set[str]:
    if not viewer or not short_ids:
        return set()

    rows = frappe.get_all(
        "AOS Short Repost",
        filters={
            "user": viewer,
            "short": ["in", short_ids],
            "status": "active",
        },
        pluck="short",
    )

    return set(rows or [])


def _load_relationship_map(
    viewer: str | None,
    target_users: list[str],
) -> dict[str, dict[str, Any]]:
    unique = sorted({str(user).strip() for user in target_users if user})
    if not unique:
        return {}
    if not viewer:
        return {
            target: _guest_relationship_payload(target_user=target)
            for target in unique
        }
    return social_relationship_map(
        repository=SocialRepository(),
        viewer=viewer,
        targets=unique,
    )


def _guest_relationship_payload(*, target_user: str | None) -> dict[str, Any]:
    return {
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
    }


def _build_viewer_state(
    row: dict[str, Any],
    *,
    viewer: str | None,
    liked_short_ids: set[str],
    saved_short_ids: set[str],
    reposted_short_ids: set[str],
    relationships: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    short_id = row.get("name")
    owner = row.get("owner")

    is_logged_in = bool(viewer)
    is_owner = bool(viewer and owner and viewer == owner)

    relationship = relationships.get(str(owner or "")) or _guest_relationship_payload(
        target_user=owner,
    )

    return {
        "is_liked": bool(short_id and short_id in liked_short_ids),
        "is_saved": bool(short_id and short_id in saved_short_ids),
        "is_reposted": bool(short_id and short_id in reposted_short_ids),
        "is_owner": is_owner,
        "can_edit": is_owner,
        "can_delete": is_owner,
        "can_report": bool(is_logged_in and not is_owner),
        "can_repost": bool(is_logged_in and not is_owner),
        "can_share": True,
        **relationship,
    }


def _serialize_rows_with_viewer_state(
    rows: list[dict[str, Any]],
    *,
    viewer: str | None,
) -> list[dict[str, Any]]:
    if not rows:
        return []

    short_ids = [
        row.get("name")
        for row in rows
        if row.get("name")
    ]

    owner_users = list(
        {
            row.get("owner")
            for row in rows
            if row.get("owner")
        }
    )

    liked_short_ids = _load_liked_short_ids(viewer, short_ids)
    saved_short_ids = _load_saved_short_ids(viewer, short_ids)
    reposted_short_ids = _load_reposted_short_ids(viewer, short_ids)
    mention_map = get_short_mentions_map(short_ids)
    sound_map = get_short_sound_map(short_ids)

    for row in rows:
        short_id = row.get("name")
        row["mentions"] = mention_map.get(short_id, [])
        row["sound"] = sound_map.get(short_id)

    relationships = _load_relationship_map(viewer, owner_users)

    return [
        serialize_short_row(
            row,
            viewer_state=_build_viewer_state(
                row,
                viewer=viewer,
                liked_short_ids=liked_short_ids,
                saved_short_ids=saved_short_ids,
                reposted_short_ids=reposted_short_ids,
                relationships=relationships,
            ),
        )
        for row in rows
    ]


def _select_short_rows_sql() -> str:
    return """
        SELECT
            s.name,
            s.owner,
            s.status,
            s.visibility_status,
            s.content_mode,
            s.audience,
            s.allow_comments,
            s.allow_downloads,
            s.caption,
            s.hashtags,
            s.playback_url,
            s.processed_file_key,
            s.processed_file_url,
            s.raw_video_media,
            s.thumbnail_media,
            s.audio_mix_status,
            s.audio_mix_error,
            s.thumbnail_url,
            s.duration_seconds,
            s.view_count,
            s.like_count,
            s.comment_count,
            s.share_count,
            s.save_count,
            s.download_count,
            s.repost_count,
            s.impression_count,
            s.ranking_score,
            s.posted_on,
            s.creation,
            s.seller,
            s.ad,

            u.full_name AS creator_name,
            u.user_image AS creator_avatar,
            COALESCE(p.is_verified, 0) AS creator_is_verified,

            ad.title AS ad_title,
            ad.price AS ad_price,
            ad.currency AS ad_currency,
            (
                SELECT adi.image
                FROM `tabAOS Ad Image` adi
                WHERE adi.parent = ad.name
                AND adi.parenttype = 'AOS Ad'
                AND adi.parentfield = 'images'
                ORDER BY adi.is_primary DESC, adi.sort_order ASC, adi.idx ASC
                LIMIT 1
            ) AS ad_thumbnail

        FROM `tabAOS Short` s
        LEFT JOIN `tabUser` u ON u.name = s.owner
        LEFT JOIN `tabAOS Profile` p ON p.user = s.owner
        LEFT JOIN `tabAOS Ad` ad ON ad.name = s.ad
    """


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
        viewer = _get_optional_viewer()

        # Fetch doc first for access control.
        doc = frappe.get_doc("AOS Short", short_id)

        # ACCESS CONTROL
        # Owner can access own draft/hidden/failed/processing short.
        # Non-owners can only access ready + visible shorts that pass audience rules.
        is_owner = bool(viewer and doc.owner == viewer)

        if not is_owner:
            if doc.status != "ready" or doc.visibility_status != "visible":
                return fail("Short not available.", error="NOT_FOUND")

            if not can_view_short(doc, current_user=viewer):
                return fail("Short not available.", error="NOT_FOUND")

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}

            WHERE s.name = %s
            """,
            (short_id,),
            as_dict=True,
        )

        if not rows:
            return fail("Short not found.", error="NOT_FOUND")

        item = _serialize_rows_with_viewer_state(rows[:1], viewer=viewer)[0]

        return ok(
            "Short fetched.",
            data={"item": item},
        )

    except frappe.DoesNotExistError:
        return fail("Short not found.", error="NOT_FOUND")

    except Exception:
        frappe.log_error("Shorts operation failed.", "get_short failed")
        return fail("Failed to fetch short", error="INTERNAL_ERROR")


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
            {_select_short_rows_sql()}

            WHERE
                s.owner = %s
                {where_cursor}

            ORDER BY
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (user, *params_cursor, limit + 1),
            as_dict=True,
        )

        if not rows:
            return ok(
                "My shorts fetched.",
                data={
                    "items": [],
                    "next_cursor": None,
                    "has_more": False,
                },
            )

        has_more = len(rows) > limit
        visible_rows = rows[:limit]

        items = _serialize_rows_with_viewer_state(visible_rows, viewer=user)

        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(
                created_on=last["creation"],
                name=last["name"],
            )

        return ok(
            "My shorts fetched.",
            data={
                "items": items,
                "next_cursor": next_cursor,
                "has_more": has_more,
            },
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "my_shorts failed")
        return fail("Failed to fetch my shorts", error="INTERNAL_ERROR")


# USER SHORTS / PROFILE SHORTS
def user_shorts_impl(**kwargs):
    """List shorts for a given user/profile.

    Behavior:
    - Owner viewing own profile sees all their shorts, including private/hidden/failed.
    - Other viewers/guests see only ready + visible shorts that pass audience rules.
    - Optional content_mode/mode filter supports shop, geo, vibes, learn, or all.
    """
    rl = rate_limit(
        key=f"aos:shorts:user_profile:ip:{request_ip()}",
        ttl_seconds=60,
        limit=120,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    target_reference, err = require_id(
        kwargs.get("user") or kwargs.get("target_user"),
        "user",
    )
    if err:
        return err
    target_user = resolve_account_reference(target_reference, allow_legacy=True)
    if not target_user:
        return fail("User not found.", error="NOT_FOUND")

    viewer = _get_optional_viewer()
    is_owner = bool(viewer and viewer == target_user)

    limit = validate_limit(
        kwargs.get("limit"),
        USER_SHORTS_DEFAULT_LIMIT,
        USER_SHORTS_MAX_LIMIT,
    )
    cursor = kwargs.get("cursor")
    content_mode = kwargs.get("content_mode") or kwargs.get("mode")

    mode_clause = ""
    mode_params: tuple = ()

    if content_mode and str(content_mode).strip().lower() != "all":
        content_mode, err = validate_content_mode(content_mode)
        if err:
            return err

        mode_clause = "AND s.content_mode = %s"
        mode_params = (content_mode,)

    try:
        where_cursor, params_cursor = build_cursor_where_clause(
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        if is_owner:
            availability_clause = ""
            availability_params: tuple = ()
        else:
            availability_clause = """
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
            """
            availability_params = ()

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}

            WHERE
                s.owner = %s
                {availability_clause}
                {mode_clause}
                {where_cursor}

            ORDER BY
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (target_user, *mode_params, *availability_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        if not is_owner:
            safe_rows = []
            for row in rows or []:
                if can_view_short(row, current_user=viewer):
                    safe_rows.append(row)
                    if len(safe_rows) >= limit + 1:
                        break
            rows = safe_rows

        if not rows:
            return ok(
                "User shorts fetched.",
                data={"items": [], "next_cursor": None, "has_more": False},
            )

        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        items = _serialize_rows_with_viewer_state(visible_rows, viewer=viewer)

        next_cursor = None
        if has_more:
            last = visible_rows[-1]
            next_cursor = build_time_id_cursor(
                created_on=last["creation"],
                name=last["name"],
            )

        return ok(
            "User shorts fetched.",
            data={"items": items, "next_cursor": next_cursor, "has_more": has_more},
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "user_shorts failed")
        return fail("Failed to fetch user shorts", error="INTERNAL_ERROR")


# DELETE SHORT (SOFT)
def delete_short_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        locked, active_jobs = ShortsRepository().lock_short_and_active_jobs(short_id)
        if not locked:
            return fail("Short not found.", error="NOT_FOUND")
        doc = frappe.get_doc("AOS Short", short_id)

        if doc.owner != user:
            return fail("Not allowed.", error="FORBIDDEN")
        if doc.status == "deleted":
            return ok("Short deleted.", data={"short_id": short_id})

        doc.visibility_status = "deleted"
        doc.status = "deleted"
        doc.save(ignore_permissions=True)
        job_names = tuple(row["name"] for row in active_jobs)
        if job_names:
            frappe.db.sql(
                """UPDATE `tabAOS Video Processing Job`
                   SET status='Cancelled', active_key=NULL, completed_at=NOW(), last_error='SHORT_DELETED'
                   WHERE name IN %(names)s""",
                {"names": job_names},
            )
            if frappe.db.table_exists("AOS Transactional Outbox"):
                frappe.db.sql(
                    """UPDATE `tabAOS Transactional Outbox`
                       SET status='Cancelled', completed_at=NOW(), last_error='SHORT_DELETED'
                       WHERE job_doctype='AOS Video Processing Job' AND job_name IN %(names)s
                         AND status NOT IN ('Completed','Completed With Failure','Failed','Dead Letter','Cancelled')""",
                    {"names": job_names},
                )
        for media_id in {str(getattr(doc, "raw_video_media", "") or ""), str(getattr(doc, "thumbnail_media", "") or "")}:
            if not media_id:
                continue
            try:
                MediaService().release_media(
                    media_id=media_id, user=user, attached_doctype="AOS Short",
                    attached_name=doc.name, system=True,
                )
            except Exception:
                frappe.log_error("Short media release failed.", "Shorts delete media release")

        return ok(
            "Short deleted.",
            data={"short_id": short_id},
        )

    except frappe.DoesNotExistError:
        return fail("Short not found.", error="NOT_FOUND")

    except Exception:
        frappe.log_error("Shorts operation failed.", "delete_short failed")
        return fail("Failed to delete short", error="INTERNAL_ERROR")


# RETRY PROCESSING
def retry_processing_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    try:
        locked, active_jobs = ShortsRepository().lock_short_and_active_jobs(short_id)
        if not locked:
            return fail("Short not found.", error="NOT_FOUND")
        doc = frappe.get_doc("AOS Short", short_id)

        if doc.owner != user:
            return fail("Not allowed.", error="FORBIDDEN")
        if active_jobs:
            return ok(
                "Processing already active.",
                data={"short_id": short_id, "video_job_id": active_jobs[-1]["name"]},
            )
        if doc.status != "failed":
            return fail(
                "Only failed shorts can be retried.",
                error="VALIDATION_ERROR",
            )

        video_job = create_video_processing_job(
            short_id=doc.name,
            force=False,
            reason="retry",
            enqueue=True,
        )

        return ok(
            "Processing restarted.",
            data={"short_id": short_id, "video_job_id": video_job.name},
        )

    except frappe.DoesNotExistError:
        return fail("Short not found.", error="NOT_FOUND")

    except Exception:
        frappe.log_error("Shorts operation failed.", "retry_processing failed")
        return fail("Failed to retry processing", error="INTERNAL_ERROR")
