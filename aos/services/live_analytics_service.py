"""
Live Analytics Service for AOS.

Responsibilities:
- Maintain lightweight counters on AOS Live Stream.
- Handle live-message comment counts.
- Handle reaction counts.
- Synchronize viewer metrics from view sessions.
- Provide repair and consistency helpers.

Important:
- AOS Live Message is the source of truth for comments and replies.
- System, co-host, gift, and moderation messages are not comments.
- Viewer count is derived from AOS Live Stream View.
"""

from __future__ import annotations

import frappe


LIVE_STREAM_DOCTYPE = "AOS Live Stream"
LIVE_MESSAGE_DOCTYPE = "AOS Live Message"
LIVE_REACTION_DOCTYPE = "AOS Live Stream Reaction"
LIVE_VIEW_DOCTYPE = "AOS Live Stream View"

ACTIVE_MESSAGE_STATUS = "active"
COMMENT_MESSAGE_KIND = "comment"

COMMENT_MESSAGE_TYPES = {
    "comment",
    "reply",
}


class LiveAnalyticsService:
    # COMMENT EVENTS
    @classmethod
    def handle_comment_added(
        cls,
        *,
        live_id: str,
    ):
        """
        Increment comment_count after one active comment or reply is inserted.

        The caller must ensure the inserted message is:
        - message_kind = comment
        - message_type = comment or reply
        - status = active

        System and co-host messages must not call this method.
        """
        if not live_id:
            return None

        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET comment_count = COALESCE(comment_count, 0) + 1
                WHERE name = %s
                """,
                (live_id,),
            )

            return cls._get_live_counter(
                live_id=live_id,
                fieldname="comment_count",
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"handle_comment_added failed for live {live_id}",
            )

            return None

    @classmethod
    def handle_comment_deleted(
        cls,
        *,
        live_id: str,
    ):
        """
        Decrement comment_count safely after one active comment is hard-deleted.

        Soft-delete and cascade-delete workflows should use
        sync_comment_count() instead because multiple rows may be affected.
        """
        if not live_id:
            return None

        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET comment_count = GREATEST(
                    COALESCE(comment_count, 0) - 1,
                    0
                )
                WHERE name = %s
                """,
                (live_id,),
            )

            return cls._get_live_counter(
                live_id=live_id,
                fieldname="comment_count",
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"handle_comment_deleted failed for live {live_id}",
            )

            return None

    @classmethod
    def sync_comment_count(
        cls,
        *,
        live_id: str,
    ):
        """
        Recalculate comment_count from its source of truth.

        Source of truth:
        - AOS Live Message
        - message_kind = comment
        - message_type in comment, reply
        - status = active

        System messages, co-host messages, gifts, moderation messages,
        hidden messages, and deleted messages are excluded.
        """
        if not live_id:
            return None

        try:
            result = frappe.db.sql(
                """
                SELECT COUNT(*) AS count
                FROM `tabAOS Live Message`
                WHERE live_stream = %s
                  AND message_kind = %s
                  AND message_type IN (%s, %s)
                  AND status = %s
                """,
                (
                    live_id,
                    COMMENT_MESSAGE_KIND,
                    "comment",
                    "reply",
                    ACTIVE_MESSAGE_STATUS,
                ),
                as_dict=True,
            )

            count = int(
                result[0].get("count") or 0
            )

            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
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

            return None

    # REACTION EVENTS
    @classmethod
    def handle_reaction(
        cls,
        *,
        live_id: str,
    ):
        """
        Increment reaction_count for one newly inserted reaction.

        Reactions are event-based, so every accepted reaction increments the
        counter.
        """
        if not live_id:
            return None

        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET reaction_count = COALESCE(reaction_count, 0) + 1
                WHERE name = %s
                """,
                (live_id,),
            )

            return cls._get_live_counter(
                live_id=live_id,
                fieldname="reaction_count",
            )

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"handle_reaction failed for live {live_id}",
            )

            return None

    @classmethod
    def sync_reaction_count(
        cls,
        *,
        live_id: str,
    ):
        """
        Recalculate reaction_count from AOS Live Stream Reaction rows.
        """
        if not live_id:
            return None

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

            count = int(
                result[0].get("count") or 0
            )

            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
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

            return None

    # VIEW EVENTS / SYNC
    @classmethod
    def sync_view_metrics(
        cls,
        *,
        live_id: str,
    ):
        """
        Recalculate viewer metrics from AOS Live Stream View.

        Source of truth:
        - viewer_count = active view sessions
        - total_views = all view-session rows
        - total_watch_time_seconds = sum of completed/current durations
        - peak_viewers = highest recorded concurrent viewer count

        peak_viewers never decreases during normal synchronization.
        """
        if not live_id:
            return None

        try:
            result = frappe.db.sql(
                """
                SELECT
                    SUM(
                        CASE
                            WHEN is_active = 1 THEN 1
                            ELSE 0
                        END
                    ) AS viewer_count,
                    COUNT(*) AS total_views,
                    COALESCE(
                        SUM(watch_duration_seconds),
                        0
                    ) AS total_watch_time_seconds
                FROM `tabAOS Live Stream View`
                WHERE live_stream = %s
                """,
                (live_id,),
                as_dict=True,
            )

            row = result[0] if result else {}

            viewer_count = int(
                row.get("viewer_count") or 0
            )

            total_views = int(
                row.get("total_views") or 0
            )

            total_watch_time_seconds = int(
                row.get("total_watch_time_seconds") or 0
            )

            current_peak = frappe.db.get_value(
                LIVE_STREAM_DOCTYPE,
                live_id,
                "peak_viewers",
            )

            peak_viewers = max(
                int(current_peak or 0),
                viewer_count,
            )

            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
                live_id,
                {
                    "viewer_count": viewer_count,
                    "total_views": total_views,
                    "peak_viewers": peak_viewers,
                    "total_watch_time_seconds": (
                        total_watch_time_seconds
                    ),
                },
                update_modified=False,
            )

            return {
                "viewer_count": viewer_count,
                "total_views": total_views,
                "peak_viewers": peak_viewers,
                "total_watch_time_seconds": (
                    total_watch_time_seconds
                ),
            }

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_view_metrics failed for live {live_id}",
            )

            return None

    # COMPLETE SYNC
    @classmethod
    def sync_live_metrics(
        cls,
        *,
        live_id: str,
    ):
        """
        Repair all derived Live Stream counters from their source tables.
        """

        if not live_id:
            return None

        comment_count = cls.sync_comment_count(
            live_id=live_id,
        )

        reaction_count = cls.sync_reaction_count(
            live_id=live_id,
        )

        view_metrics = cls.sync_view_metrics(
            live_id=live_id,
        )

        return {
            "comment_count": comment_count,
            "reaction_count": reaction_count,
            "view_metrics": view_metrics,
        }

    # INTERNAL HELPERS
    @staticmethod
    def _get_live_counter(
        *,
        live_id: str,
        fieldname: str,
    ) -> int | None:
        """
        Return a numeric counter from AOS Live Stream.
        """

        value = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            fieldname,
        )

        if value is None:
            return None

        return int(value or 0)
