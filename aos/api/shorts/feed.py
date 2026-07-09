"""
Feed APIs for Shorts.

Handles:
- For You feed
- Following feed
- Ad-specific feed
"""

from __future__ import annotations
from typing import Any

import frappe

from aos.api.shared.auth import require_login, current_user
from aos.api.shared.rate_limit import rate_limit, request_ip
from aos.api.shared.responses import ok, fail
from aos.api.shared.validators import require_id
from aos.api.shared.sql_safety import clean_safe_docnames

from aos.api.shorts.validators import validate_limit, validate_content_mode
from aos.api.shorts.constants import (
    FEED_DEFAULT_LIMIT,
    FEED_MAX_LIMIT,
    FEED_LIMIT_PER_MINUTE_PER_IP,
    SHORT_CONTENT_MODE_SHOP,
    SHORT_AUDIENCE_EVERYONE,
    SHORT_AUDIENCE_FOLLOWERS,
    SHORT_AUDIENCE_FRIENDS,
)

from aos.api.shorts.utils import (
    build_ranked_cursor,
    build_ranked_cursor_where_clause,
    serialize_short_row,
)

from aos.api.shorts.visibility import can_view_short
from aos.api.shorts.mentions import get_short_mentions_map
from aos.api.shorts.sounds import get_short_sound_map
from aos.services.search_ranking_service import short_feed_candidates


# COMMON
def _get_limit(kwargs):
    return validate_limit(
        kwargs.get("limit"),
        FEED_DEFAULT_LIMIT,
        FEED_MAX_LIMIT,
    )


def _get_optional_viewer() -> str | None:
    """
    Resolve viewer for public feed endpoints.

    Public endpoints may be called by guests or logged-in users.
    Logged-in users should receive viewer_state.
    Guests receive stable false/default viewer_state.
    """
    user = current_user()
    if not user or user == "Guest":
        return None

    return user


def _short_candidate_sql(short_ids: list[str]) -> tuple[str, str]:
    cleaned = clean_safe_docnames(short_ids)
    if not cleaned:
        return "", ""

    escaped = ", ".join(frappe.db.escape(short_id) for short_id in cleaned)
    return f"AND s.name in ({escaped})", f"FIELD(s.name, {escaped}) ASC"


def _build_content_mode_filter(content_mode):
    if not content_mode or str(content_mode).strip().lower() == "all":
        return "", (), None

    mode, err = validate_content_mode(content_mode)
    if err:
        return "", (), err

    return "AND s.content_mode = %s", (mode,), None


def _build_audience_where_clause(viewer: str | None) -> tuple[str, tuple]:
    """
    SQL-level audience filter.

    This is the first privacy layer. The Python can_view_short() check remains
    as the final safety layer before serialization.

    Guest:
    - only everyone

    Logged-in user:
    - everyone
    - own shorts
    - followers shorts where viewer follows creator
    - friends shorts where viewer and creator mutually follow each other
    """
    if not viewer:
        return "AND s.audience = %s", (SHORT_AUDIENCE_EVERYONE,)

    return (
        """
        AND (
            s.audience = %s
            OR s.owner = %s
            OR (
                s.audience = %s
                AND EXISTS (
                    SELECT 1
                    FROM `tabAOS Follow` af
                    WHERE
                        af.follower_user = %s
                        AND af.following_user = s.owner
                    LIMIT 1
                )
            )
            OR (
                s.audience = %s
                AND EXISTS (
                    SELECT 1
                    FROM `tabAOS Follow` af1
                    WHERE
                        af1.follower_user = %s
                        AND af1.following_user = s.owner
                    LIMIT 1
                )
                AND EXISTS (
                    SELECT 1
                    FROM `tabAOS Follow` af2
                    WHERE
                        af2.follower_user = s.owner
                        AND af2.following_user = %s
                    LIMIT 1
                )
            )
        )
        """,
        (
            SHORT_AUDIENCE_EVERYONE,
            viewer,
            SHORT_AUDIENCE_FOLLOWERS,
            viewer,
            SHORT_AUDIENCE_FRIENDS,
            viewer,
            viewer,
        ),
    )


def _filter_viewable_rows(
    rows: list[dict[str, Any]],
    *,
    viewer: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """
    Final Python safety filter for audience visibility.

    SQL already filters audience for performance, but this guarantees no row is
    exposed if a SQL clause changes later.
    """
    result = []

    for row in rows or []:
        if can_view_short(row, current_user=viewer):
            result.append(row)

            if len(result) >= limit + 1:
                break

    return result


def _load_liked_short_ids(viewer: str | None, short_ids: list[str]) -> set[str]:
    """
    Batch-load short IDs liked by the current viewer.

    Avoids N+1 queries during feed serialization.
    """
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
    """Batch-load short IDs saved by the current viewer."""
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
    """Batch-load short IDs reposted by the current viewer."""
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

def _load_followed_user_ids(
    viewer: str | None,
    target_users: list[str],
) -> set[str]:
    """
    Batch-load users followed by the current viewer.

    AOS Follow is user-to-user:
    - follower_user = current viewer
    - following_user = short.owner
    """
    if not viewer or not target_users:
        return set()

    rows = frappe.get_all(
        "AOS Follow",
        filters={
            "follower_user": viewer,
            "following_user": ["in", target_users],
        },
        pluck="following_user",
    )

    return set(rows or [])


def _load_followed_by_user_ids(
    viewer: str | None,
    target_users: list[str],
) -> set[str]:
    """
    Batch-load users who follow the current viewer.

    AOS Follow is user-to-user:
    - follower_user = short.owner
    - following_user = current viewer

    This lets the UI show:
    - Follow Back
    - Friends
    """
    if not viewer or not target_users:
        return set()

    rows = frappe.get_all(
        "AOS Follow",
        filters={
            "follower_user": ["in", target_users],
            "following_user": viewer,
        },
        pluck="follower_user",
    )

    return set(rows or [])


def _build_relationship_payload(
    *,
    viewer: str | None,
    target_user: str | None,
    followed_user_ids: set[str],
    followed_by_user_ids: set[str],
) -> dict[str, Any]:
    """
    Build relationship state for a short creator.

    Meaning:
    - is_following: viewer follows creator
    - is_followed_by: creator follows viewer
    - is_friend: both follow each other
    """

    if not target_user:
        return _guest_relationship_payload(target_user=None)

    if not viewer:
        return _guest_relationship_payload(target_user=target_user)

    if viewer == target_user:
        return {
            "target_user": target_user,
            "is_self": True,
            "is_following": False,
            "is_followed_by": False,
            "is_friend": False,
            "relationship_status": "none",
            "action_label": "You",
        }

    is_following = target_user in followed_user_ids
    is_followed_by = target_user in followed_by_user_ids
    is_friend = is_following and is_followed_by

    if is_friend:
        relationship_status = "friends"
        action_label = "Friends"
    elif is_following:
        relationship_status = "following"
        action_label = "Following"
    elif is_followed_by:
        relationship_status = "followed_by"
        action_label = "Follow Back"
    else:
        relationship_status = "none"
        action_label = "Follow"

    return {
        "target_user": target_user,
        "is_self": False,
        "is_following": is_following,
        "is_followed_by": is_followed_by,
        "is_friend": is_friend,
        "relationship_status": relationship_status,
        "action_label": action_label,
    }


def _guest_relationship_payload(*, target_user: str | None) -> dict[str, Any]:
    return {
        "target_user": target_user,
        "is_self": False,
        "is_following": False,
        "is_followed_by": False,
        "is_friend": False,
        "relationship_status": "none",
        "action_label": "Follow",
    }


def _build_viewer_state(
    row: dict[str, Any],
    *,
    viewer: str | None,
    liked_short_ids: set[str],
    saved_short_ids: set[str],
    reposted_short_ids: set[str],
    followed_user_ids: set[str],
    followed_by_user_ids: set[str],
) -> dict[str, Any]:
    """
    Build viewer_state for one short from preloaded sets.

    Important:
    - owner is the creator/poster user.
    - following is based on AOS Follow.following_user = short.owner.
    - seller is optional shop context and is not used for relationship state.
    """
    short_id = row.get("name")
    owner = row.get("owner")

    is_logged_in = bool(viewer)
    is_owner = bool(viewer and owner and viewer == owner)

    relationship = _build_relationship_payload(
        viewer=viewer,
        target_user=owner,
        followed_user_ids=followed_user_ids,
        followed_by_user_ids=followed_by_user_ids,
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


def _build_response(rows, limit: int, *, viewer: str | None = None):
    if not rows:
        return ok(
            "Feed fetched.",
            data={
                "items": [],
                "next_cursor": None,
                "has_more": False,
            },
        )

    safe_rows = _filter_viewable_rows(rows, viewer=viewer, limit=limit)

    if not safe_rows:
        return ok(
            "Feed fetched.",
            data={
                "items": [],
                "next_cursor": None,
                "has_more": False,
            },
        )

    has_more = len(safe_rows) > limit
    visible_rows = safe_rows[:limit]

    short_ids = [
        row.get("name")
        for row in visible_rows
        if row.get("name")
    ]

    owner_users = list(
        {
            row.get("owner")
            for row in visible_rows
            if row.get("owner")
        }
    )

    liked_short_ids = _load_liked_short_ids(viewer, short_ids)
    saved_short_ids = _load_saved_short_ids(viewer, short_ids)
    reposted_short_ids = _load_reposted_short_ids(viewer, short_ids)
    mention_map = get_short_mentions_map(short_ids)
    sound_map = get_short_sound_map(short_ids)

    for row in visible_rows:
        short_id = row.get("name")
        row["mentions"] = mention_map.get(short_id, [])
        row["sound"] = sound_map.get(short_id)

    followed_user_ids = _load_followed_user_ids(viewer, owner_users)
    followed_by_user_ids = _load_followed_by_user_ids(viewer, owner_users)

    items = [
        serialize_short_row(
            row,
            viewer_state=_build_viewer_state(
                row,
                viewer=viewer,
                liked_short_ids=liked_short_ids,
                saved_short_ids=saved_short_ids,
                reposted_short_ids=reposted_short_ids,
                followed_user_ids=followed_user_ids,
                followed_by_user_ids=followed_by_user_ids,
            ),
        )
        for row in visible_rows
    ]

    next_cursor = None
    if has_more:
        last = visible_rows[-1]
        next_cursor = build_ranked_cursor(
            ranking_score=last.get("ranking_score"),
            created_on=last.get("creation"),
            name=last.get("name"),
        )

    return ok(
        "Feed fetched.",
        data={
            "items": items,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    )


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


# FEED: FOR YOU
def feed_for_you_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:feed:ip:{request_ip()}",
        ttl_seconds=60,
        limit=FEED_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        viewer = _get_optional_viewer()
        limit = _get_limit(kwargs)
        cursor = kwargs.get("cursor")

        mode_clause, mode_params, mode_err = _build_content_mode_filter(
            kwargs.get("content_mode") or kwargs.get("mode")
        )
        if mode_err:
            return mode_err

        candidate_clause = ""
        candidate_order_sql = ""
        if not cursor:
            try:
                candidate_short_ids = clean_safe_docnames(
                    short_feed_candidates(
                        viewer=viewer,
                        content_mode=kwargs.get("content_mode") or kwargs.get("mode"),
                        limit=limit + 1,
                        offset=0,
                    )
                )
                if candidate_short_ids:
                    candidate_clause, candidate_order_sql = _short_candidate_sql(candidate_short_ids)
                else:
                    return _build_response([], limit, viewer=viewer)
            except Exception:
                candidate_clause = ""
                candidate_order_sql = ""
                frappe.log_error(
                    frappe.get_traceback(),
                    "AOS Search Ranking Shorts Candidate Fetch Failed",
                )

        audience_clause, audience_params = _build_audience_where_clause(viewer)

        where_cursor, params_cursor = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}

            WHERE
                s.status = 'ready'
                AND s.visibility_status = 'visible'
                {mode_clause}
                {audience_clause}
                {candidate_clause}
                {where_cursor}

            ORDER BY
                {candidate_order_sql + "," if candidate_order_sql else ""}
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (*mode_params, *audience_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit, viewer=viewer)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_for_you failed")
        return fail("Failed to fetch feed", error="INTERNAL_ERROR")


# FEED: FOLLOWING
def feed_following_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:following:user:{user}",
        ttl_seconds=60,
        limit=FEED_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    try:
        limit = _get_limit(kwargs)
        cursor = kwargs.get("cursor")

        mode_clause, mode_params, mode_err = _build_content_mode_filter(
            kwargs.get("content_mode") or kwargs.get("mode")
        )
        if mode_err:
            return mode_err

        audience_clause, audience_params = _build_audience_where_clause(user)

        where_cursor, params_cursor = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}
            INNER JOIN `tabAOS Follow` f ON f.following_user = s.owner

            WHERE
                f.follower_user = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {mode_clause}
                {audience_clause}
                {where_cursor}

            ORDER BY
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (user, *mode_params, *audience_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit, viewer=user)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_following failed")
        return fail("Failed to fetch following feed", error="INTERNAL_ERROR")


# FEED: BY AD
def feed_by_ad_impl(**kwargs):
    rl = rate_limit(
        key=f"aos:shorts:by_ad:ip:{request_ip()}",
        ttl_seconds=60,
        limit=FEED_LIMIT_PER_MINUTE_PER_IP,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad_id, err = require_id(kwargs.get("ad_id"), "ad_id")
    if err:
        return err

    try:
        viewer = _get_optional_viewer()
        limit = _get_limit(kwargs)
        cursor = kwargs.get("cursor")

        audience_clause, audience_params = _build_audience_where_clause(viewer)

        where_cursor, params_cursor = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        rows = frappe.db.sql(
            f"""
            {_select_short_rows_sql()}

            WHERE
                s.ad = %s
                AND s.content_mode = %s
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {audience_clause}
                {where_cursor}

            ORDER BY
                s.ranking_score DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (
                ad_id,
                SHORT_CONTENT_MODE_SHOP,
                *audience_params,
                *params_cursor,
                limit + 1,
            ),
            as_dict=True,
        )

        return _build_response(rows, limit, viewer=viewer)

    except Exception:
        frappe.log_error(frappe.get_traceback(), "feed_by_ad failed")
        return fail("Failed to fetch ad feed", error="INTERNAL_ERROR")
