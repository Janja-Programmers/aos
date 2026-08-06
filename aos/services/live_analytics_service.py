"""Canonical, transaction-safe Live engagement counter service."""

from __future__ import annotations

import frappe

LIVE_STREAM_DOCTYPE = "AOS Live Stream"
ACTIVE_MESSAGE_STATUS = "active"
COMMENT_MESSAGE_KIND = "comment"


class LiveAnalyticsService:
    """Maintain derived counters without committing or exposing private values."""

    @staticmethod
    def _log_failure(operation: str) -> None:
        frappe.log_error("Live analytics operation failed.", f"Live analytics: {operation}")

    @classmethod
    def handle_comment_added(cls, *, live_id: str):
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
            return cls._get_live_counter(live_id=live_id, fieldname="comment_count")
        except Exception:
            cls._log_failure("comment_increment")
            return None

    @classmethod
    def handle_comment_deleted(cls, *, live_id: str):
        if not live_id:
            return None
        try:
            frappe.db.sql(
                """
                UPDATE `tabAOS Live Stream`
                SET comment_count = GREATEST(COALESCE(comment_count, 0) - 1, 0)
                WHERE name = %s
                """,
                (live_id,),
            )
            return cls._get_live_counter(live_id=live_id, fieldname="comment_count")
        except Exception:
            cls._log_failure("comment_decrement")
            return None

    @classmethod
    def sync_comment_count(cls, *, live_id: str):
        if not live_id:
            return None
        try:
            rows = frappe.db.sql(
                """
                SELECT COUNT(*) AS count
                FROM `tabAOS Live Message`
                WHERE live_stream = %s
                  AND message_kind = %s
                  AND message_type IN ('comment', 'reply')
                  AND status = %s
                """,
                (live_id, COMMENT_MESSAGE_KIND, ACTIVE_MESSAGE_STATUS),
                as_dict=True,
            )
            count = max(0, int((rows[0] if rows else {}).get("count") or 0))
            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
                live_id,
                "comment_count",
                count,
                update_modified=False,
            )
            return count
        except Exception:
            cls._log_failure("comment_reconcile")
            return None

    @classmethod
    def handle_reaction(cls, *, live_id: str):
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
            return cls._get_live_counter(live_id=live_id, fieldname="reaction_count")
        except Exception:
            cls._log_failure("reaction_increment")
            return None

    @classmethod
    def sync_reaction_count(cls, *, live_id: str):
        if not live_id:
            return None
        try:
            rows = frappe.db.sql(
                """
                SELECT COUNT(*) AS count
                FROM `tabAOS Live Stream Reaction`
                WHERE live_stream = %s
                """,
                (live_id,),
                as_dict=True,
            )
            count = max(0, int((rows[0] if rows else {}).get("count") or 0))
            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
                live_id,
                "reaction_count",
                count,
                update_modified=False,
            )
            return count
        except Exception:
            cls._log_failure("reaction_reconcile")
            return None

    @classmethod
    def sync_view_metrics(cls, *, live_id: str):
        """Reconcile authoritative presence and historical view metrics.

        ``viewer_count`` is concurrent non-host view sessions.
        ``peak_viewers`` is the maximum concurrent count observed.
        ``unique_viewers`` is distinct authenticated accounts plus distinct
        guest sessions. ``total_joins`` counts all session rows. The legacy
        ``total_views`` remains an alias of total joins for compatibility.
        """
        if not live_id:
            return None
        try:
            rows = frappe.db.sql(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END), 0) AS viewer_count,
                    COUNT(*) AS total_joins,
                    COUNT(DISTINCT CASE
                        WHEN NULLIF(user, '') IS NOT NULL THEN CONCAT('u:', user)
                        ELSE CONCAT('g:', session_id)
                    END) AS unique_viewers,
                    COALESCE(SUM(GREATEST(COALESCE(watch_duration_seconds, 0), 0)), 0)
                        AS total_watch_time_seconds
                FROM `tabAOS Live Stream View`
                WHERE live_stream = %s
                """,
                (live_id,),
                as_dict=True,
            )
            row = rows[0] if rows else {}
            viewer_count = max(0, int(row.get("viewer_count") or 0))
            total_joins = max(0, int(row.get("total_joins") or 0))
            unique_viewers = max(0, int(row.get("unique_viewers") or 0))
            watch_time = max(0, int(row.get("total_watch_time_seconds") or 0))
            current_peak = max(
                0,
                int(
                    frappe.db.get_value(
                        LIVE_STREAM_DOCTYPE,
                        live_id,
                        "peak_viewers",
                    )
                    or 0
                ),
            )
            peak_viewers = max(current_peak, viewer_count)
            values = {
                "viewer_count": viewer_count,
                "total_views": total_joins,
                "total_joins": total_joins,
                "unique_viewers": unique_viewers,
                "peak_viewers": peak_viewers,
                "total_watch_time_seconds": watch_time,
            }
            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
                live_id,
                values,
                update_modified=False,
            )
            return values
        except Exception:
            cls._log_failure("viewer_reconcile")
            return None

    @classmethod
    def sync_live_metrics(cls, *, live_id: str):
        if not live_id:
            return None
        return {
            "comment_count": cls.sync_comment_count(live_id=live_id),
            "reaction_count": cls.sync_reaction_count(live_id=live_id),
            "view_metrics": cls.sync_view_metrics(live_id=live_id),
        }

    @staticmethod
    def _get_live_counter(*, live_id: str, fieldname: str) -> int | None:
        value = frappe.db.get_value(LIVE_STREAM_DOCTYPE, live_id, fieldname)
        return None if value is None else max(0, int(value or 0))
