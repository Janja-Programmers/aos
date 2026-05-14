"""
Live Analytics Service for AOS.

Responsibilities:
- Maintain lightweight counters on AOS Live Stream
- Handle comments and reactions
- Provide sync helpers for repair/consistency

Important:
- Viewer count is not increment/decrement based.
- Viewer count is derived from AOS Live Stream View as source of truth.
"""

from __future__ import annotations

import frappe


ACTIVE_COMMENT_STATUS = "active"


class LiveAnalyticsService:
    # COMMENT EVENTS
    @classmethod
    def handle_comment_added(cls, *, live_id: str):
        """
        Increment comment count.

        Use this for normal insert paths where one active comment was added.
        """
        if not live_id:
            return

        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET comment_count = COALESCE(comment_count, 0) + 1
                WHERE name = %s
                """,
                (live_id,),
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"handle_comment_added failed for live {live_id}",
            )

    @classmethod
    def handle_comment_deleted(cls, *, live_id: str):
        """
        Decrement comment count safely.

        Use this only for hard-delete paths where exactly one active comment
        was removed.
        """
        if not live_id:
            return

        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET comment_count = GREATEST(COALESCE(comment_count, 0) - 1, 0)
                WHERE name = %s
                """,
                (live_id,),
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"handle_comment_deleted failed for live {live_id}",
            )

    @classmethod
    def sync_comment_count(cls, *, live_id: str):
        """
        Recalculate comment count from source of truth.

        Source of truth:
        - AOS Live Stream Comment
        - status = active

        This is the safest method after soft-deletes, cascade deletes,
        moderation changes, or repair jobs.
        """
        if not live_id:
            return

        try:
            result = frappe.db.sql(
                """
                SELECT COUNT(*) AS count
                FROM `tabAOS Live Stream Comment`
                WHERE live_stream = %s
                  AND status = %s
                """,
                (live_id, ACTIVE_COMMENT_STATUS),
                as_dict=True,
            )

            count = int(result[0].get("count") or 0)

            frappe.db.set_value(
                "AOS Live Stream",
                live_id,
                "comment_count",
                count,
                update_modified=False,
            )

            return count

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_comment_count failed for live {live_id}",
            )

    # REACTION EVENTS
    @classmethod
    def handle_reaction(cls, *, live_id: str):
        """
        Increment reaction count.

        Reactions are event-based, so every inserted reaction increments
        reaction_count.
        """
        if not live_id:
            return

        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET reaction_count = COALESCE(reaction_count, 0) + 1
                WHERE name = %s
                """,
                (live_id,),
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"handle_reaction failed for live {live_id}",
            )

    @classmethod
    def sync_reaction_count(cls, *, live_id: str):
        """
        Recalculate reaction count from source of truth.

        Source of truth:
        - AOS Live Stream Reaction rows
        """
        if not live_id:
            return

        try:
            result = frappe.db.sql(
                """
                SELECT COUNT(*) AS count
                FROM `tabAOS Live Stream Reaction`
                WHERE live_stream = %s
                """,
                (live_id,),
                as_dict=True,
            )

            count = int(result[0].get("count") or 0)

            frappe.db.set_value(
                "AOS Live Stream",
                live_id,
                "reaction_count",
                count,
                update_modified=False,
            )

            return count

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_reaction_count failed for live {live_id}",
            )

    # VIEW EVENTS / SYNC
    @classmethod
    def sync_view_metrics(cls, *, live_id: str):
        """
        Recalculate viewer metrics from AOS Live Stream View.

        Source of truth:
        - viewer_count = active view sessions
        - total_views = total view sessions
        - total_watch_time_seconds = sum watch duration
        - peak_viewers is preserved unless current active count is higher
        """
        if not live_id:
            return

        try:
            active_result = frappe.db.sql(
                """
                SELECT COUNT(*) AS count
                FROM `tabAOS Live Stream View`
                WHERE live_stream = %s
                  AND is_active = 1
                """,
                (live_id,),
                as_dict=True,
            )

            total_result = frappe.db.sql(
                """
                SELECT
                    COUNT(*) AS total_views,
                    COALESCE(SUM(watch_duration_seconds), 0) AS total_watch_time_seconds
                FROM `tabAOS Live Stream View`
                WHERE live_stream = %s
                """,
                (live_id,),
                as_dict=True,
            )

            viewer_count = int(active_result[0].get("count") or 0)
            total_views = int(total_result[0].get("total_views") or 0)
            total_watch_time_seconds = int(
                total_result[0].get("total_watch_time_seconds") or 0
            )

            current_peak = frappe.db.get_value(
                "AOS Live Stream",
                live_id,
                "peak_viewers",
            ) or 0

            peak_viewers = max(int(current_peak or 0), viewer_count)

            frappe.db.set_value(
                "AOS Live Stream",
                live_id,
                {
                    "viewer_count": viewer_count,
                    "total_views": total_views,
                    "peak_viewers": peak_viewers,
                    "total_watch_time_seconds": total_watch_time_seconds,
                },
                update_modified=False,
            )

            return {
                "viewer_count": viewer_count,
                "total_views": total_views,
                "peak_viewers": peak_viewers,
                "total_watch_time_seconds": total_watch_time_seconds,
            }

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_view_metrics failed for live {live_id}",
            )
