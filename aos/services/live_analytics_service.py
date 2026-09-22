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
        """Increment the active comment counter without writing the Live row."""
        if not live_id:
            return None
        from aos.services.live.ephemeral import (
            change_comment_count,
            current_comment_count,
            schedule_hot_counter_materialization,
        )

        current = current_comment_count(live_id=live_id)
        base = current if current is not None else cls._get_live_counter(
            live_id=live_id, fieldname="comment_count"
        )
        count = change_comment_count(live_id=live_id, base=int(base or 0), delta=1)
        schedule_hot_counter_materialization(live_id=live_id)
        return count

    @classmethod
    def handle_comment_deleted(cls, *, live_id: str, count: int = 1):
        if not live_id:
            return None
        from aos.services.live.ephemeral import (
            change_comment_count,
            current_comment_count,
            schedule_hot_counter_materialization,
        )

        current = current_comment_count(live_id=live_id)
        base = current if current is not None else cls._get_live_counter(
            live_id=live_id, fieldname="comment_count"
        )
        updated = change_comment_count(
            live_id=live_id,
            base=int(base or 0),
            delta=-max(0, int(count or 0)),
        )
        schedule_hot_counter_materialization(live_id=live_id)
        return updated

    @classmethod
    def get_comment_count(cls, *, live_id: str) -> int | None:
        if not live_id:
            return None
        from aos.services.live.ephemeral import current_comment_count

        cached = current_comment_count(live_id=live_id)
        if cached is not None:
            return cached
        return cls._get_live_counter(live_id=live_id, fieldname="comment_count")

    @classmethod
    def materialize_comment_count(cls, *, live_id: str):
        """Flush the Redis comment counter outside participant locks."""
        if not live_id:
            return None
        count = cls.get_comment_count(live_id=live_id)
        if count is None:
            return None
        frappe.db.set_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            "comment_count",
            max(0, int(count or 0)),
            update_modified=False,
        )
        return max(0, int(count or 0))

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
            from aos.services.live.ephemeral import set_comment_count

            set_comment_count(live_id=live_id, count=count)
            return count
        except Exception:
            cls._log_failure("comment_reconcile")
            return None

    @classmethod
    def sync_reaction_count(cls, *, live_id: str):
        if not live_id:
            return None
        try:
            # Reaction taps are Redis-aggregated to avoid one MariaDB insert
            # per tap. The materialized Live aggregate is the durable fallback
            # after cache loss or expiry.
            from aos.services.live.ephemeral import current_reaction_total

            count = current_reaction_total(live_id=live_id)
            if count is None:
                count = max(
                    0,
                    int(
                        frappe.db.get_value(
                            LIVE_STREAM_DOCTYPE, live_id, "reaction_count"
                        )
                        or 0
                    ),
                )
            frappe.db.set_value(
                LIVE_STREAM_DOCTYPE,
                live_id,
                "reaction_count",
                max(0, int(count or 0)),
                update_modified=False,
            )
            return max(0, int(count or 0))
        except Exception:
            cls._log_failure("reaction_reconcile")
            return None

    @classmethod
    def _materialized_view_metrics(cls, *, live_id: str) -> dict[str, int] | None:
        if not live_id:
            return None
        row = frappe.db.get_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            [
                "viewer_count",
                "total_views",
                "total_joins",
                "unique_viewers",
                "peak_viewers",
                "total_watch_time_seconds",
            ],
            as_dict=True,
        )
        if not row:
            return None
        return {
            "viewer_count": max(0, int(row.viewer_count or 0)),
            "total_views": max(0, int(row.total_views or 0)),
            "total_joins": max(0, int(row.total_joins or 0)),
            "unique_viewers": max(0, int(row.unique_viewers or 0)),
            "peak_viewers": max(0, int(row.peak_viewers or 0)),
            "total_watch_time_seconds": max(0, int(row.total_watch_time_seconds or 0)),
        }

    @classmethod
    def get_view_metrics(cls, *, live_id: str) -> dict[str, int] | None:
        """Read Redis hot metrics first, then the materialized Live row."""
        if not live_id:
            return None
        from aos.services.live.ephemeral import current_view_metrics

        cached = current_view_metrics(live_id=live_id)
        return cached if cached is not None else cls._materialized_view_metrics(live_id=live_id)

    @classmethod
    def handle_view_joined(cls, *, live_id: str):
        """Increment hot-path view metrics atomically in Redis."""
        if not live_id:
            return None
        from aos.services.live.ephemeral import increment_view_metrics

        base = cls._materialized_view_metrics(live_id=live_id) or {}
        metrics = increment_view_metrics(live_id=live_id, base=base)
        if metrics is not None:
            return metrics

        # Redis degradation must stay O(1): do not convert a connection storm
        # into one COUNT(*) query per join. Return a local approximation from
        # the last materialized snapshot; reconciliation restores authority.
        fallback = dict(base)
        fallback["viewer_count"] = max(0, int(fallback.get("viewer_count") or 0) + 1)
        fallback["total_views"] = max(0, int(fallback.get("total_views") or 0) + 1)
        fallback["total_joins"] = max(0, int(fallback.get("total_joins") or 0) + 1)
        fallback["peak_viewers"] = max(
            int(fallback.get("peak_viewers") or 0),
            int(fallback["viewer_count"]),
        )
        return fallback

    @classmethod
    def handle_view_left(cls, *, live_id: str, watch_duration_seconds: int = 0):
        return cls.handle_views_left(
            live_id=live_id,
            count=1,
            watch_duration_seconds=watch_duration_seconds,
        )

    @classmethod
    def handle_views_left(
        cls,
        *,
        live_id: str,
        count: int,
        watch_duration_seconds: int = 0,
    ):
        if not live_id:
            return None
        from aos.services.live.ephemeral import decrement_view_metrics

        base = cls._materialized_view_metrics(live_id=live_id) or {}
        metrics = decrement_view_metrics(
            live_id=live_id,
            base=base,
            count=max(0, int(count or 0)),
            watch_duration_seconds=max(0, int(watch_duration_seconds or 0)),
        )
        if metrics is not None:
            return metrics
        # Same bounded degradation rule as joins: avoid a database COUNT(*)
        # per disconnect while Redis is impaired. Durable reconciliation fixes
        # the approximation and persists watch-time totals later.
        fallback = dict(base)
        fallback["viewer_count"] = max(
            0,
            int(fallback.get("viewer_count") or 0) - max(0, int(count or 0)),
        )
        return fallback

    @classmethod
    def materialize_view_metrics(cls, *, live_id: str):
        """Flush Redis view metrics to the Live row outside participant locks."""
        if not live_id:
            return None
        from aos.services.live.ephemeral import current_view_metrics

        metrics = current_view_metrics(live_id=live_id)
        if metrics is None:
            return cls._materialized_view_metrics(live_id=live_id)
        frappe.db.set_value(
            LIVE_STREAM_DOCTYPE,
            live_id,
            metrics,
            update_modified=False,
        )
        return metrics

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
            state = frappe.db.get_value(
                LIVE_STREAM_DOCTYPE, live_id, ["status", "is_active"], as_dict=True
            )
            if state and (str(state.status or "") == "ended" or not bool(state.is_active)):
                from aos.services.live.ephemeral import clear_view_metrics

                clear_view_metrics(live_id=live_id)
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
