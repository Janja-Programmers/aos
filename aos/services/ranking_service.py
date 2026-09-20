"""
Ranking Service for Shorts.

Responsible for:
- Computing ranking score
- Applying engagement weights
- Applying recency decay
- Updating AOS Short ranking_score

NO API logic here.
"""

from __future__ import annotations

import math
from datetime import datetime

import frappe
from frappe.utils import now_datetime


class RankingService:
    # CONFIG (TUNE THESE)
    VIEW_WEIGHT = 1.0
    LIKE_WEIGHT = 3.0
    COMMENT_WEIGHT = 5.0
    SHARE_WEIGHT = 7.0
    REPOST_WEIGHT = 8.0
    WATCH_WEIGHT = 10.0

    DECAY_FACTOR_HOURS = 48  # bigger = slower decay

    # PUBLIC ENTRY
    @classmethod
    def update_short_score(cls, short_id: str):
        """
        Recalculate ranking score for a single short.
        """
        try:
            short = frappe.db.get_value(
                "AOS Short",
                short_id,
                [
                    "view_count",
                    "like_count",
                    "comment_count",
                    "share_count",
                    "repost_count",
                    "duration_seconds",
                    "creation",
                ],
                as_dict=True,
            )

            if not short:
                return

            # Aggregate watch time
            total_watch_ms = cls._get_total_watch_time(short_id)

            score = cls._compute_score(short, total_watch_ms)

            frappe.db.set_value(
                "AOS Short",
                short_id,
                "ranking_score",
                score,
                update_modified=False,
            )

            try:
                from aos.services.search_ranking_service import enqueue_short_search_index
                enqueue_short_search_index(
                    short_id,
                    source="short_ranking_update",
                )
            except frappe.QueryDeadlockError:
                # MariaDB rolls the transaction back on deadlock. Swallowing it
                # would make the background job look successful even though
                # the ranking_score write was lost.
                raise
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Search/ranking short index refresh failed for {short_id}",
                )

        except frappe.QueryDeadlockError:
            raise
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "RankingService.update_short_score failed",
            )

    # CORE COMPUTATION
    @classmethod
    def _compute_score(cls, short: dict, total_watch_ms: int) -> float:
        views = float(short.get("view_count") or 0)
        likes = float(short.get("like_count") or 0)
        comments = float(short.get("comment_count") or 0)
        shares = float(short.get("share_count") or 0)
        reposts = float(short.get("repost_count") or 0)
        duration = float(short.get("duration_seconds") or 1)

        # WATCH RATIO
        avg_watch_ms = total_watch_ms / views if views > 0 else 0
        watch_ratio = min(avg_watch_ms / (duration * 1000), 1.0)

        # BASE SCORE
        score = (
            views * cls.VIEW_WEIGHT
            + likes * cls.LIKE_WEIGHT
            + comments * cls.COMMENT_WEIGHT
            + shares * cls.SHARE_WEIGHT
            + reposts * cls.REPOST_WEIGHT
            + watch_ratio * cls.WATCH_WEIGHT
        )

        # APPLY DECAY
        created = short.get("creation")
        age_hours = cls._get_age_hours(created)

        decay = math.exp(-age_hours / cls.DECAY_FACTOR_HOURS)

        return score * decay

    # WATCH TIME AGGREGATION
    @staticmethod
    def _get_total_watch_time(short_id: str) -> int:
        """
        Sum watch time from AOS Short View.
        """
        result = frappe.db.sql(
            """
            SELECT SUM(watch_ms) as total
            FROM `tabAOS Short View`
            WHERE short = %s
            """,
            (short_id,),
            as_dict=True,
        )

        return int(result[0].get("total") or 0)

    # TIME HELPERS
    @staticmethod
    def _get_age_hours(created: datetime) -> float:
        if not created:
            return 0.0

        now = now_datetime()

        delta = now - created

        return delta.total_seconds() / 3600.0

    # BULK UPDATE (CRON/JOB)
    @classmethod
    def update_batch(cls, limit: int = 100):
        """
        Recalculate scores for multiple shorts.
        Used by background jobs.
        """
        try:
            shorts = frappe.get_all(
                "AOS Short",
                filters={"lifecycle_status": "Published", "moderation_status": "Approved", "processing_status": ["in", ["Ready", "Not Required"]]},
                fields=["name"],
                limit=limit,
                order_by="modified desc",
            )

            for s in shorts:
                cls.update_short_score(s.name)

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                "RankingService.update_batch failed",
            )
