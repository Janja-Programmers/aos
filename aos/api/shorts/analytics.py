"""Analytics APIs for Shorts.

Handles:
- per-short analytics
- creator/my shorts analytics
- user shorts analytics for self/admin
- general/admin shorts analytics
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.accounts.identity import public_account_id_for_user, resolve_account_reference
from frappe.utils import add_days, date_diff, getdate, today

from aos.api.shared.auth import require_login
from aos.api.shared.formatters import humanize_count
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.api.shared.validators import require_id
from aos.api.shorts.constants import (
    ANALYTICS_DEFAULT_RANGE_DAYS,
    ANALYTICS_MAX_RANGE_DAYS,
    ANALYTICS_TOP_DEFAULT_LIMIT,
    ANALYTICS_TOP_MAX_LIMIT,
    GENERAL_SHORT_ANALYTICS_LIMIT_PER_MINUTE_PER_USER,
    SHORT_ANALYTICS_LIMIT_PER_MINUTE_PER_USER,
)
from aos.api.shorts.validators import validate_limit


METRIC_FIELDS = (
    "impressions",
    "views",
    "watch_time_ms",
    "avg_watch_time_ms",
    "likes",
    "comments",
    "shares",
    "saves",
    "downloads",
    "reposts",
)

ENGAGEMENT_FIELDS = (
    "likes",
    "comments",
    "shares",
    "saves",
    "downloads",
    "reposts",
)

STAFF_ROLES = {"System Manager", "AOS Moderator"}


# PUBLIC IMPLEMENTATIONS

def get_short_analytics_impl(**kwargs):
    """Return date-ranged analytics for one short.

    Access:
    - short owner
    - System Manager / AOS Moderator
    """
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:shorts:analytics:short:user:{user}",
        ttl_seconds=60,
        limit=SHORT_ANALYTICS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    short_id, err = require_id(kwargs.get("short_id"), "short_id")
    if err:
        return err

    date_from, date_to, err = _normalize_date_range(
        kwargs.get("date_from"),
        kwargs.get("date_to"),
    )
    if err:
        return err

    try:
        short = _get_short_summary(short_id)
        if not short:
            return fail("Short not found.", error="NOT_FOUND")

        if short.owner != user and not _is_staff(user):
            return fail("Not allowed.", error="FORBIDDEN")

        daily = _get_daily_rows(
            where_clause="m.short = %s AND m.date BETWEEN %s AND %s",
            params=(short_id, date_from, date_to),
        )
        totals = _build_totals(daily)

        return ok(
            "Short analytics fetched.",
            data={
                "short": _serialize_short_summary(short),
                "date_from": str(date_from),
                "date_to": str(date_to),
                "totals": totals,
                "current_totals": _serialize_current_short_totals(short),
                "daily": daily,
            },
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "get_short_analytics failed")
        return fail("Failed to fetch short analytics", error="INTERNAL_ERROR")


def my_shorts_analytics_impl(**kwargs):
    """Return creator analytics for the logged-in user."""
    user, err = require_login()
    if err:
        return err

    return _creator_analytics_response(
        viewer=user,
        target_user=user,
        kwargs=kwargs,
        message="My shorts analytics fetched.",
    )


def user_short_analytics_impl(**kwargs):
    """Return shorts analytics for a user.

    Access:
    - the same user
    - System Manager / AOS Moderator
    """
    user, err = require_login()
    if err:
        return err

    target_reference, err = require_id(
        kwargs.get("user") or kwargs.get("target_user") or public_account_id_for_user(user),
        "user",
    )
    if err:
        return err
    target_user = resolve_account_reference(target_reference, allow_legacy=True)
    if not target_user:
        return fail("User not found.", error="NOT_FOUND")

    if target_user != user and not _is_staff(user):
        return fail("Not allowed.", error="FORBIDDEN")

    if not frappe.db.exists("User", target_user):
        return fail("User not found.", error="NOT_FOUND")

    return _creator_analytics_response(
        viewer=user,
        target_user=target_user,
        kwargs=kwargs,
        message="User shorts analytics fetched.",
    )


def general_short_analytics_impl(**kwargs):
    """Return platform-wide Shorts analytics for admins/moderators."""
    user, err = require_login()
    if err:
        return err

    if not _is_staff(user):
        return fail("Not allowed.", error="FORBIDDEN")

    rl = rate_limit(
        key=f"aos:shorts:analytics:general:user:{user}",
        ttl_seconds=60,
        limit=GENERAL_SHORT_ANALYTICS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    date_from, date_to, err = _normalize_date_range(
        kwargs.get("date_from"),
        kwargs.get("date_to"),
    )
    if err:
        return err

    top_limit = validate_limit(
        kwargs.get("top_limit") or kwargs.get("limit"),
        ANALYTICS_TOP_DEFAULT_LIMIT,
        ANALYTICS_TOP_MAX_LIMIT,
    )

    try:
        daily = _get_daily_rows(
            where_clause="m.date BETWEEN %s AND %s",
            params=(date_from, date_to),
        )
        totals = _build_totals(daily)

        return ok(
            "General shorts analytics fetched.",
            data={
                "date_from": str(date_from),
                "date_to": str(date_to),
                "totals": totals,
                "overview": _get_general_overview(date_from, date_to),
                "daily": daily,
                "top_shorts": _get_top_shorts(
                    date_from=date_from,
                    date_to=date_to,
                    limit=top_limit,
                ),
                "top_creators": _get_top_creators(
                    date_from=date_from,
                    date_to=date_to,
                    limit=top_limit,
                ),
                "content_mode_breakdown": _get_content_mode_breakdown(
                    date_from=date_from,
                    date_to=date_to,
                ),
                "country_breakdown": _get_country_breakdown(
                    date_from=date_from,
                    date_to=date_to,
                    limit=top_limit,
                ),
            },
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "general_short_analytics failed")
        return fail("Failed to fetch general shorts analytics", error="INTERNAL_ERROR")


# CREATOR ANALYTICS

def _creator_analytics_response(
    *,
    viewer: str,
    target_user: str,
    kwargs: dict[str, Any],
    message: str,
):
    rl = rate_limit(
        key=f"aos:shorts:analytics:user:{viewer}:target:{target_user}",
        ttl_seconds=60,
        limit=SHORT_ANALYTICS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    date_from, date_to, err = _normalize_date_range(
        kwargs.get("date_from"),
        kwargs.get("date_to"),
    )
    if err:
        return err

    top_limit = validate_limit(
        kwargs.get("top_limit") or kwargs.get("limit"),
        ANALYTICS_TOP_DEFAULT_LIMIT,
        ANALYTICS_TOP_MAX_LIMIT,
    )

    try:
        daily = _get_daily_rows(
            where_clause="s.owner = %s AND m.date BETWEEN %s AND %s",
            params=(target_user, date_from, date_to),
            join_short=True,
        )
        totals = _build_totals(daily)

        return ok(
            message,
            data={
                "user": _serialize_user(target_user),
                "date_from": str(date_from),
                "date_to": str(date_to),
                "totals": totals,
                "overview": _get_creator_overview(target_user, date_from, date_to),
                "daily": daily,
                "top_shorts": _get_top_shorts(
                    date_from=date_from,
                    date_to=date_to,
                    limit=top_limit,
                    owner=target_user,
                ),
                "content_mode_breakdown": _get_content_mode_breakdown(
                    date_from=date_from,
                    date_to=date_to,
                    owner=target_user,
                ),
                "country_breakdown": _get_country_breakdown(
                    date_from=date_from,
                    date_to=date_to,
                    limit=top_limit,
                    owner=target_user,
                ),
            },
        )

    except Exception:
        frappe.log_error("Shorts operation failed.", "creator short analytics failed")
        return fail("Failed to fetch user shorts analytics", error="INTERNAL_ERROR")


# DATE / ACCESS HELPERS

def _normalize_date_range(date_from=None, date_to=None):
    try:
        normalized_to = getdate(date_to) if date_to else getdate(today())
        normalized_from = (
            getdate(date_from)
            if date_from
            else add_days(normalized_to, -(ANALYTICS_DEFAULT_RANGE_DAYS - 1))
        )
    except Exception:
        return None, None, fail("Invalid date range.", error="VALIDATION_ERROR")

    if normalized_from > normalized_to:
        return None, None, fail(
            "date_from cannot be after date_to.",
            error="VALIDATION_ERROR",
        )

    if date_diff(normalized_to, normalized_from) > ANALYTICS_MAX_RANGE_DAYS:
        return None, None, fail(
            f"Analytics range cannot exceed {ANALYTICS_MAX_RANGE_DAYS} days.",
            error="VALIDATION_ERROR",
        )

    return normalized_from, normalized_to, None


def _is_staff(user: str) -> bool:
    roles = set(frappe.get_roles(user) or [])
    return bool(roles.intersection(STAFF_ROLES))


# SERIALIZATION HELPERS

def _serialize_user(user: str) -> dict[str, Any]:
    display = get_user_display(user)
    return {
        "user": display.get("user"),
        "display_name": display.get("display_name"),
        "avatar": display.get("avatar"),
        "is_deleted": bool(display.get("is_deleted")),
        "is_live": bool(display.get("is_live")) if not display.get("is_deleted") else False,
        "live_id": display.get("live_id") if not display.get("is_deleted") else None,
    }


def _serialize_short_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("name"),
        "owner": public_account_id_for_user(row.get("owner")),
        "caption": row.get("caption") or "",
        "content_mode": row.get("content_mode"),
        "status": row.get("status"),
        "visibility_status": row.get("visibility_status"),
        "thumbnail_url": row.get("thumbnail_url"),
        "playback_url": row.get("playback_url"),
        "duration_seconds": float(row.get("duration_seconds") or 0),
        "posted_on": row.get("posted_on"),
        "creation": row.get("creation"),
    }


def _serialize_current_short_totals(row: dict[str, Any]) -> dict[str, Any]:
    totals = {
        "impressions": int(row.get("impression_count") or 0),
        "views": int(row.get("view_count") or 0),
        "likes": int(row.get("like_count") or 0),
        "comments": int(row.get("comment_count") or 0),
        "shares": int(row.get("share_count") or 0),
        "saves": int(row.get("save_count") or 0),
        "downloads": int(row.get("download_count") or 0),
        "reposts": int(row.get("repost_count") or 0),
    }
    totals["engagements"] = sum(totals[field] for field in ENGAGEMENT_FIELDS)
    return _with_displays(totals)


def _with_displays(metrics: dict[str, Any]) -> dict[str, Any]:
    result = dict(metrics)
    for key in (
        "impressions",
        "views",
        "watch_time_ms",
        "avg_watch_time_ms",
        "likes",
        "comments",
        "shares",
        "saves",
        "downloads",
        "reposts",
        "engagements",
        "total_shorts",
        "new_shorts",
        "active_creators",
        "reported_shorts",
        "failed_processing_count",
    ):
        if key in result:
            result[f"{key}_display"] = humanize_count(result.get(key) or 0)

    if "watch_time_ms" in result:
        result["watch_time_display"] = _format_duration_ms(result.get("watch_time_ms") or 0)
    if "avg_watch_time_ms" in result:
        result["avg_watch_time_display"] = _format_duration_ms(
            result.get("avg_watch_time_ms") or 0
        )

    return result


def _format_duration_ms(value) -> str:
    try:
        seconds = int((int(value or 0)) / 1000)
    except Exception:
        seconds = 0

    if seconds <= 0:
        return "0s"

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


# DATA HELPERS

def _get_short_summary(short_id: str) -> dict[str, Any] | None:
    return frappe.db.get_value(
        "AOS Short",
        short_id,
        [
            "name",
            "owner",
            "caption",
            "content_mode",
            "status",
            "visibility_status",
            "thumbnail_url",
            "playback_url",
            "duration_seconds",
            "posted_on",
            "creation",
            "impression_count",
            "view_count",
            "like_count",
            "comment_count",
            "share_count",
            "save_count",
            "download_count",
            "repost_count",
        ],
        as_dict=True,
    )


def _get_daily_rows(
    *,
    where_clause: str,
    params: tuple,
    join_short: bool = False,
) -> list[dict[str, Any]]:
    join_sql = "INNER JOIN `tabAOS Short` s ON s.name = m.short" if join_short else ""

    rows = frappe.db.sql(
        f"""
        SELECT
            m.date,
            COALESCE(SUM(m.impressions), 0) AS impressions,
            COALESCE(SUM(m.views), 0) AS views,
            COALESCE(SUM(m.watch_time_ms), 0) AS watch_time_ms,
            COALESCE(SUM(m.likes), 0) AS likes,
            COALESCE(SUM(m.comments), 0) AS comments,
            COALESCE(SUM(m.shares), 0) AS shares,
            COALESCE(SUM(m.saves), 0) AS saves,
            COALESCE(SUM(m.downloads), 0) AS downloads,
            COALESCE(SUM(m.reposts), 0) AS reposts,
            CASE
                WHEN COALESCE(SUM(m.views), 0) > 0
                THEN COALESCE(SUM(m.watch_time_ms), 0) / COALESCE(SUM(m.views), 0)
                ELSE 0
            END AS avg_watch_time_ms,
            CASE
                WHEN COALESCE(SUM(m.views), 0) > 0
                THEN COALESCE(SUM(m.completion_rate * m.views), 0) / COALESCE(SUM(m.views), 0)
                ELSE 0
            END AS completion_rate
        FROM `tabAOS Short Metrics Daily` m
        {join_sql}
        WHERE {where_clause}
        GROUP BY m.date
        ORDER BY m.date ASC
        """,
        params,
        as_dict=True,
    )

    return [_serialize_daily_row(row) for row in rows]


def _serialize_daily_row(row: dict[str, Any]) -> dict[str, Any]:
    metrics = {
        "date": str(row.get("date")),
        "impressions": int(row.get("impressions") or 0),
        "views": int(row.get("views") or 0),
        "watch_time_ms": int(row.get("watch_time_ms") or 0),
        "avg_watch_time_ms": int(row.get("avg_watch_time_ms") or 0),
        "likes": int(row.get("likes") or 0),
        "comments": int(row.get("comments") or 0),
        "shares": int(row.get("shares") or 0),
        "saves": int(row.get("saves") or 0),
        "downloads": int(row.get("downloads") or 0),
        "reposts": int(row.get("reposts") or 0),
        "completion_rate": float(row.get("completion_rate") or 0),
    }
    metrics["engagements"] = sum(metrics[field] for field in ENGAGEMENT_FIELDS)
    return _with_displays(metrics)


def _build_totals(daily_rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {field: 0 for field in METRIC_FIELDS}

    weighted_completion = 0.0
    weighted_views = 0

    for row in daily_rows:
        for field in METRIC_FIELDS:
            if field == "avg_watch_time_ms":
                continue
            totals[field] += int(row.get(field) or 0)

        row_views = int(row.get("views") or 0)
        if row_views:
            weighted_views += row_views
            weighted_completion += float(row.get("completion_rate") or 0) * row_views

    totals["avg_watch_time_ms"] = (
        int(totals["watch_time_ms"] / totals["views"])
        if totals["views"]
        else 0
    )
    totals["completion_rate"] = (
        weighted_completion / weighted_views
        if weighted_views
        else 0
    )
    totals["engagements"] = sum(totals[field] for field in ENGAGEMENT_FIELDS)
    totals["engagement_rate"] = (
        totals["engagements"] / totals["impressions"]
        if totals["impressions"]
        else 0
    )

    return _with_displays(totals)


def _get_creator_overview(target_user: str, date_from, date_to) -> dict[str, Any]:
    status_rows = frappe.db.sql(
        """
        SELECT status, COUNT(*) AS total
        FROM `tabAOS Short`
        WHERE owner = %s
        GROUP BY status
        """,
        (target_user,),
        as_dict=True,
    )
    status_counts = {row.status or "unknown": int(row.total or 0) for row in status_rows}

    total_shorts = sum(status_counts.values())
    new_shorts = frappe.db.sql(
        """
        SELECT COUNT(*) AS total
        FROM `tabAOS Short`
        WHERE owner = %s
          AND DATE(creation) BETWEEN %s AND %s
        """,
        (target_user, date_from, date_to),
        as_dict=True,
    )[0].total or 0

    overview = {
        "total_shorts": int(total_shorts or 0),
        "new_shorts": int(new_shorts or 0),
        "status_counts": status_counts,
    }
    return _with_displays(overview)


def _get_general_overview(date_from, date_to) -> dict[str, Any]:
    total_shorts = frappe.db.sql(
        """
        SELECT COUNT(*) AS total
        FROM `tabAOS Short`
        WHERE status != 'deleted'
        """,
        as_dict=True,
    )[0].total or 0

    new_shorts = frappe.db.sql(
        """
        SELECT COUNT(*) AS total
        FROM `tabAOS Short`
        WHERE DATE(creation) BETWEEN %s AND %s
        """,
        (date_from, date_to),
        as_dict=True,
    )[0].total or 0

    active_creators = frappe.db.sql(
        """
        SELECT COUNT(DISTINCT s.owner) AS total
        FROM `tabAOS Short Metrics Daily` m
        INNER JOIN `tabAOS Short` s ON s.name = m.short
        WHERE m.date BETWEEN %s AND %s
          AND (
            m.views > 0
            OR m.impressions > 0
            OR m.likes > 0
            OR m.comments > 0
            OR m.shares > 0
            OR m.saves > 0
            OR m.downloads > 0
            OR m.reposts > 0
          )
        """,
        (date_from, date_to),
        as_dict=True,
    )[0].total or 0

    failed_processing_count = frappe.db.count("AOS Short", {"status": "failed"})

    reported_shorts = 0
    if frappe.db.exists("DocType", "AOS Short Report"):
        reported_shorts = frappe.db.sql(
            """
            SELECT COUNT(*) AS total
            FROM `tabAOS Short Report`
            WHERE DATE(creation) BETWEEN %s AND %s
            """,
            (date_from, date_to),
            as_dict=True,
        )[0].total or 0

    overview = {
        "total_shorts": int(total_shorts or 0),
        "new_shorts": int(new_shorts or 0),
        "active_creators": int(active_creators or 0),
        "reported_shorts": int(reported_shorts or 0),
        "failed_processing_count": int(failed_processing_count or 0),
    }
    return _with_displays(overview)


def _get_top_shorts(*, date_from, date_to, limit: int, owner: str | None = None):
    owner_clause = "AND s.owner = %s" if owner else ""
    params: tuple = (date_from, date_to, owner, limit) if owner else (date_from, date_to, limit)

    rows = frappe.db.sql(
        f"""
        SELECT
            s.name,
            s.owner,
            s.caption,
            s.content_mode,
            s.status,
            s.visibility_status,
            s.thumbnail_url,
            s.playback_url,
            s.duration_seconds,
            s.posted_on,
            s.creation,
            COALESCE(SUM(m.impressions), 0) AS impressions,
            COALESCE(SUM(m.views), 0) AS views,
            COALESCE(SUM(m.watch_time_ms), 0) AS watch_time_ms,
            COALESCE(SUM(m.likes), 0) AS likes,
            COALESCE(SUM(m.comments), 0) AS comments,
            COALESCE(SUM(m.shares), 0) AS shares,
            COALESCE(SUM(m.saves), 0) AS saves,
            COALESCE(SUM(m.downloads), 0) AS downloads,
            COALESCE(SUM(m.reposts), 0) AS reposts
        FROM `tabAOS Short` s
        LEFT JOIN `tabAOS Short Metrics Daily` m
          ON m.short = s.name
         AND m.date BETWEEN %s AND %s
        WHERE s.status != 'deleted'
          {owner_clause}
        GROUP BY
            s.name,
            s.owner,
            s.caption,
            s.content_mode,
            s.status,
            s.visibility_status,
            s.thumbnail_url,
            s.playback_url,
            s.duration_seconds,
            s.posted_on,
            s.creation
        ORDER BY
            views DESC,
            likes DESC,
            comments DESC,
            shares DESC,
            s.creation DESC
        LIMIT %s
        """,
        params,
        as_dict=True,
    )

    items = []
    for row in rows:
        metrics = {
            "impressions": int(row.get("impressions") or 0),
            "views": int(row.get("views") or 0),
            "watch_time_ms": int(row.get("watch_time_ms") or 0),
            "likes": int(row.get("likes") or 0),
            "comments": int(row.get("comments") or 0),
            "shares": int(row.get("shares") or 0),
            "saves": int(row.get("saves") or 0),
            "downloads": int(row.get("downloads") or 0),
            "reposts": int(row.get("reposts") or 0),
        }
        metrics["engagements"] = sum(metrics[field] for field in ENGAGEMENT_FIELDS)
        item = _serialize_short_summary(row)
        item["metrics"] = _with_displays(metrics)
        items.append(item)

    return items


def _get_top_creators(*, date_from, date_to, limit: int):
    rows = frappe.db.sql(
        """
        SELECT
            s.owner,
            COUNT(DISTINCT s.name) AS total_shorts,
            COALESCE(SUM(m.impressions), 0) AS impressions,
            COALESCE(SUM(m.views), 0) AS views,
            COALESCE(SUM(m.watch_time_ms), 0) AS watch_time_ms,
            COALESCE(SUM(m.likes), 0) AS likes,
            COALESCE(SUM(m.comments), 0) AS comments,
            COALESCE(SUM(m.shares), 0) AS shares,
            COALESCE(SUM(m.saves), 0) AS saves,
            COALESCE(SUM(m.downloads), 0) AS downloads,
            COALESCE(SUM(m.reposts), 0) AS reposts
        FROM `tabAOS Short` s
        LEFT JOIN `tabAOS Short Metrics Daily` m
          ON m.short = s.name
         AND m.date BETWEEN %s AND %s
        WHERE s.status != 'deleted'
        GROUP BY s.owner
        ORDER BY views DESC, likes DESC, comments DESC, shares DESC
        LIMIT %s
        """,
        (date_from, date_to, limit),
        as_dict=True,
    )

    items = []
    for row in rows:
        metrics = {
            "total_shorts": int(row.get("total_shorts") or 0),
            "impressions": int(row.get("impressions") or 0),
            "views": int(row.get("views") or 0),
            "watch_time_ms": int(row.get("watch_time_ms") or 0),
            "likes": int(row.get("likes") or 0),
            "comments": int(row.get("comments") or 0),
            "shares": int(row.get("shares") or 0),
            "saves": int(row.get("saves") or 0),
            "downloads": int(row.get("downloads") or 0),
            "reposts": int(row.get("reposts") or 0),
        }
        metrics["engagements"] = sum(metrics[field] for field in ENGAGEMENT_FIELDS)
        items.append({"user": _serialize_user(row.owner), "metrics": _with_displays(metrics)})

    return items


def _get_content_mode_breakdown(*, date_from, date_to, owner: str | None = None):
    owner_clause = "AND s.owner = %s" if owner else ""
    params: tuple = (date_from, date_to, owner) if owner else (date_from, date_to)

    rows = frappe.db.sql(
        f"""
        SELECT
            COALESCE(NULLIF(s.content_mode, ''), 'vibes') AS content_mode,
            COUNT(DISTINCT s.name) AS total_shorts,
            COALESCE(SUM(m.impressions), 0) AS impressions,
            COALESCE(SUM(m.views), 0) AS views,
            COALESCE(SUM(m.watch_time_ms), 0) AS watch_time_ms,
            COALESCE(SUM(m.likes), 0) AS likes,
            COALESCE(SUM(m.comments), 0) AS comments,
            COALESCE(SUM(m.shares), 0) AS shares,
            COALESCE(SUM(m.saves), 0) AS saves,
            COALESCE(SUM(m.downloads), 0) AS downloads,
            COALESCE(SUM(m.reposts), 0) AS reposts
        FROM `tabAOS Short` s
        LEFT JOIN `tabAOS Short Metrics Daily` m
          ON m.short = s.name
         AND m.date BETWEEN %s AND %s
        WHERE s.status != 'deleted'
          {owner_clause}
        GROUP BY COALESCE(NULLIF(s.content_mode, ''), 'vibes')
        ORDER BY views DESC, total_shorts DESC
        """,
        params,
        as_dict=True,
    )

    return [_serialize_breakdown_row(row, group_key="content_mode") for row in rows]


def _get_country_breakdown(*, date_from, date_to, limit: int, owner: str | None = None):
    owner_clause = "AND s.owner = %s" if owner else ""
    params: tuple = (date_from, date_to, owner, limit) if owner else (date_from, date_to, limit)

    rows = frappe.db.sql(
        f"""
        SELECT
            COALESCE(s.country, 'Unknown') AS country,
            COUNT(DISTINCT s.name) AS total_shorts,
            COALESCE(SUM(m.impressions), 0) AS impressions,
            COALESCE(SUM(m.views), 0) AS views,
            COALESCE(SUM(m.watch_time_ms), 0) AS watch_time_ms,
            COALESCE(SUM(m.likes), 0) AS likes,
            COALESCE(SUM(m.comments), 0) AS comments,
            COALESCE(SUM(m.shares), 0) AS shares,
            COALESCE(SUM(m.saves), 0) AS saves,
            COALESCE(SUM(m.downloads), 0) AS downloads,
            COALESCE(SUM(m.reposts), 0) AS reposts
        FROM `tabAOS Short` s
        LEFT JOIN `tabAOS Short Metrics Daily` m
          ON m.short = s.name
         AND m.date BETWEEN %s AND %s
        WHERE s.status != 'deleted'
          {owner_clause}
        GROUP BY COALESCE(s.country, 'Unknown')
        ORDER BY views DESC, total_shorts DESC
        LIMIT %s
        """,
        params,
        as_dict=True,
    )

    return [_serialize_breakdown_row(row, group_key="country") for row in rows]


def _serialize_breakdown_row(row: dict[str, Any], *, group_key: str) -> dict[str, Any]:
    metrics = {
        "total_shorts": int(row.get("total_shorts") or 0),
        "impressions": int(row.get("impressions") or 0),
        "views": int(row.get("views") or 0),
        "watch_time_ms": int(row.get("watch_time_ms") or 0),
        "likes": int(row.get("likes") or 0),
        "comments": int(row.get("comments") or 0),
        "shares": int(row.get("shares") or 0),
        "saves": int(row.get("saves") or 0),
        "downloads": int(row.get("downloads") or 0),
        "reposts": int(row.get("reposts") or 0),
    }
    metrics["engagements"] = sum(metrics[field] for field in ENGAGEMENT_FIELDS)
    return {group_key: row.get(group_key), "metrics": _with_displays(metrics)}
