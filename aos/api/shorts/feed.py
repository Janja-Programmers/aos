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
from aos.services.shorts.policy import audience_sql, filter_viewable_rows
from aos.api.shorts.mentions import get_short_mentions_map
from aos.api.shorts.sounds import get_short_sound_map
from aos.services.accounts.identity import public_account_id_for_user
from aos.services.search_ranking_service import short_feed_candidates
from aos.services.social.repository import SocialRepository
from aos.services.social.serializers import relationship_map as social_relationship_map


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
    """Compatibility helper backed by the canonical Shorts policy."""
    return audience_sql(viewer, short_alias="s")


def _filter_viewable_rows(
    rows: list[dict[str, Any]],
    *,
    viewer: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Final batched policy filter; SQL remains the first privacy boundary."""
    return filter_viewable_rows(rows, viewer=viewer, limit=limit + 1)


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

def _load_relationship_map(
    viewer: str | None,
    target_users: list[str],
) -> dict[str, dict[str, Any]]:
    """Batch-load block-aware canonical Social state for short creators."""
    if not viewer or not target_users:
        return {}

    return social_relationship_map(
        repository=SocialRepository(),
        viewer=viewer,
        targets=target_users,
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

    relationship = relationships.get(owner) or _guest_relationship_payload(
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

    relationships = _load_relationship_map(viewer, owner_users)

    items = [
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
            s.approval_status,
            s.content_mode,
            s.classification_status,
            s.classification_source,
            s.classification_confidence,
            s.classification_model_version,
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

        # Candidate retrieval is advisory only. It must never narrow the canonical
        # feed or change cursor ordering; an empty/partial ranking response falls
        # back to the complete deterministic database feed.
        if not cursor:
            try:
                short_feed_candidates(
                    viewer=viewer,
                    content_mode=kwargs.get("content_mode") or kwargs.get("mode"),
                    limit=limit + 1,
                    offset=0,
                )
            except Exception:
                frappe.log_error(
                    "Short ranking candidate lookup failed; database fallback used.",
                    "AOS Shorts Ranking Fallback",
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
                {where_cursor}

            ORDER BY
                COALESCE(s.ranking_score, 0) DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (*mode_params, *audience_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit, viewer=viewer)

    except Exception:
        frappe.log_error("Shorts operation failed.", "feed_for_you failed")
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

            WHERE
                EXISTS (
                    SELECT 1 FROM `tabAOS Follow` f
                    WHERE f.follower_user = %s AND f.following_user = s.owner
                )
                AND s.status = 'ready'
                AND s.visibility_status = 'visible'
                {mode_clause}
                {audience_clause}
                {where_cursor}

            ORDER BY
                COALESCE(s.ranking_score, 0) DESC,
                s.creation DESC,
                s.name DESC

            LIMIT %s
            """,
            (user, *mode_params, *audience_params, *params_cursor, limit + 1),
            as_dict=True,
        )

        return _build_response(rows, limit, viewer=user)

    except Exception:
        frappe.log_error("Shorts operation failed.", "feed_following failed")
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
                COALESCE(s.ranking_score, 0) DESC,
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
        frappe.log_error("Shorts operation failed.", "feed_by_ad failed")
        return fail("Failed to fetch ad feed", error="INTERNAL_ERROR")
