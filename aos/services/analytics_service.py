from __future__ import annotations

import frappe
from frappe.utils import getdate


class AnalyticsService:
    @classmethod
    def aggregate_short_metrics(cls, short_id: str, date=None):
        """
        Aggregate daily metrics for a single short.
        """
        if not date:
            date = getdate()

        try:
            # Daily metrics
            impressions = cls._get_event_count(short_id, date, "impression")
            shares = cls._get_event_count(short_id, date, "share")
            completed_views = cls._get_event_count(short_id, date, "complete")

            views = cls._get_view_count(short_id, date)
            watch_time_ms = cls._get_watch_time(short_id, date)
            likes = cls._get_like_count(short_id, date)
            comments = cls._get_comment_count(short_id, date)

            avg_watch_time = (watch_time_ms / views) if views else 0
            completion_rate = (completed_views / views) if views else 0

            cls._upsert_metrics(
                short_id=short_id,
                date=date,
                impressions=impressions,
                views=views,
                watch_time_ms=watch_time_ms,
                avg_watch_time=avg_watch_time,
                completion_rate=completion_rate,
                likes=likes,
                comments=comments,
                shares=shares,
            )

            # Refresh denormalized totals
            cls.refresh_short_totals(short_id)

            # enqueue via tasks layer
            try:
                frappe.enqueue(
                    "aos.api.shorts.tasks.update_short_score_task",
                    short_id=short_id,
                    queue="short",
                )
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    "AnalyticsService ranking enqueue failed",
                )

            frappe.db.commit()

        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                frappe.get_traceback(),
                f"Metrics aggregation failed for short {short_id}",
            )
            raise

    @classmethod
    def aggregate_all_shorts(cls, date=None, limit=1000):
        """Aggregate metrics for ready shorts in batches."""
        if not date:
            date = getdate()

        shorts = frappe.get_all(
            "AOS Short",
            filters={"status": "ready"},
            fields=["name"],
            limit_page_length=limit,
            order_by="modified desc",
        )

        for short_doc in shorts:
            try:
                cls.aggregate_short_metrics(short_doc.name, date)
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Metrics aggregation failed for short {short_doc.name}",
                )

    @classmethod
    def refresh_short_totals(cls, short_id: str):
        """Refresh denormalized totals stored on AOS Short."""
        try:
            impression_count = cls._get_total_event_count(short_id, "impression")
            share_count = cls._get_total_event_count(short_id, "share")
            like_count = cls._get_total_like_count(short_id)
            comment_count = cls._get_total_comment_count(short_id)
            view_count = cls._get_total_view_count(short_id)

            frappe.db.set_value(
                "AOS Short",
                short_id,
                {
                    "impression_count": impression_count,
                    "share_count": share_count,
                    "like_count": like_count,
                    "comment_count": comment_count,
                    "view_count": view_count,
                },
                update_modified=False,
            )
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Failed to refresh totals for short {short_id}",
            )
            raise

    # DAILY QUERIES
    @staticmethod
    def _get_event_count(short_id, date, event_type):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Event`
            WHERE short = %s
              AND event_type = %s
              AND DATE(creation) = %s
            """,
            (short_id, event_type, date),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    @staticmethod
    def _get_watch_time(short_id, date):
        result = frappe.db.sql(
            """
            SELECT COALESCE(SUM(watch_ms), 0) AS total
            FROM `tabAOS Short View`
            WHERE short = %s
              AND view_date = %s
            """,
            (short_id, date),
            as_dict=True,
        )
        return int(result[0].get("total") or 0)

    @staticmethod
    def _get_view_count(short_id, date):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short View`
            WHERE short = %s
              AND view_date = %s
            """,
            (short_id, date),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    @staticmethod
    def _get_like_count(short_id, date):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Like`
            WHERE short = %s
              AND DATE(creation) = %s
            """,
            (short_id, date),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    @staticmethod
    def _get_comment_count(short_id, date):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Comment`
            WHERE short = %s
              AND status = 'active'
              AND DATE(creation) = %s
            """,
            (short_id, date),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    # TOTAL QUERIES
    @staticmethod
    def _get_total_event_count(short_id, event_type):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Event`
            WHERE short = %s
              AND event_type = %s
            """,
            (short_id, event_type),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    @staticmethod
    def _get_total_view_count(short_id):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short View`
            WHERE short = %s
            """,
            (short_id,),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    @staticmethod
    def _get_total_like_count(short_id):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Like`
            WHERE short = %s
            """,
            (short_id,),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    @staticmethod
    def _get_total_comment_count(short_id):
        result = frappe.db.sql(
            """
            SELECT COUNT(*) AS count
            FROM `tabAOS Short Comment`
            WHERE short = %s
              AND status = 'active'
            """,
            (short_id,),
            as_dict=True,
        )
        return int(result[0].get("count") or 0)

    # UPSERT DAILY METRICS
    @classmethod
    def _upsert_metrics(
        cls,
        short_id,
        date,
        impressions,
        views,
        watch_time_ms,
        avg_watch_time,
        completion_rate,
        likes,
        comments,
        shares,
    ):
        existing = frappe.db.get_value(
            "AOS Short Metrics Daily",
            {"short": short_id, "date": date},
            "name",
        )

        if existing:
            frappe.db.sql(
                """
                UPDATE `tabAOS Short Metrics Daily`
                SET impressions = %s,
                    views = %s,
                    watch_time_ms = %s,
                    avg_watch_time_ms = %s,
                    completion_rate = %s,
                    likes = %s,
                    comments = %s,
                    shares = %s
                WHERE name = %s
                """,
                (
                    impressions,
                    views,
                    watch_time_ms,
                    avg_watch_time,
                    completion_rate,
                    likes,
                    comments,
                    shares,
                    existing,
                ),
            )
        else:
            doc = frappe.get_doc({
                "doctype": "AOS Short Metrics Daily",
                "short": short_id,
                "date": date,
                "impressions": impressions,
                "views": views,
                "watch_time_ms": watch_time_ms,
                "avg_watch_time_ms": avg_watch_time,
                "completion_rate": completion_rate,
                "likes": likes,
                "comments": comments,
                "shares": shares,
            })
            doc.insert(ignore_permissions=True)
