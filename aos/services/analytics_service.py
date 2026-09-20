from __future__ import annotations

import frappe
from frappe.utils import getdate

from aos.services.shorts.hot_metrics import daily_snapshot, flush_hot_metrics


class AnalyticsService:
    """Durable daily Shorts aggregates built from bounded semantic events.

    High-frequency watch/impression counters are held as cumulative Redis hot
    state and reconciled separately; no playback heartbeat writes the Short row.
    """

    @classmethod
    def aggregate_short_metrics(cls, short_id: str, date=None):
        day = getdate(date) if date else getdate()
        hot = daily_snapshot(short_id, str(day))
        views = cls._event_count(short_id, day, "qualified_view")
        completions = cls._event_count(short_id, day, "complete")
        rewatches = cls._event_count(short_id, day, "rewatch")
        early_skips = cls._event_count(short_id, day, "early_skip")
        shares = cls._event_count(short_id, day, "share")
        downloads = cls._event_count(short_id, day, "download")
        likes = cls._table_count("AOS Short Like", short_id, day)
        comments = cls._table_count("AOS Short Comment", short_id, day, extra="AND status='active'")
        saves = cls._table_count("AOS Short Save", short_id, day)
        reposts = cls._table_count("AOS Short Repost", short_id, day)
        unique_viewers = cls._unique_viewers(short_id, day)
        watch_time_ms = int(hot.get("watch_time_ms") or 0)
        impressions = int(hot.get("impression_count") or 0)
        avg_watch = (watch_time_ms / views) if views else 0
        completion_rate = (completions / views) if views else 0
        cls._upsert_metrics(
            short_id=short_id, date=day, impressions=impressions, views=views,
            unique_viewers=unique_viewers, watch_time_ms=watch_time_ms,
            avg_watch_time=avg_watch, completion_rate=completion_rate,
            rewatches=rewatches, early_skips=early_skips, likes=likes,
            comments=comments, shares=shares, saves=saves, downloads=downloads,
            reposts=reposts,
        )
        cls.refresh_short_totals(short_id)
        try:
            frappe.enqueue(
                "aos.tasks.shorts.update_single_short_ranking", short_id=short_id,
                queue="short", enqueue_after_commit=True,
            )
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AnalyticsService ranking enqueue failed")

    @classmethod
    def aggregate_all_shorts(cls, date=None, limit=1000):
        day = getdate(date) if date else getdate()
        rows = frappe.get_all(
            "AOS Short", filters={"lifecycle_status": ["!=", "Deleted"]},
            fields=["name"], limit=max(1, min(int(limit), 5000)), order_by="modified desc",
        )
        for row in rows:
            try:
                cls.aggregate_short_metrics(row.name, day)
            except Exception:
                frappe.log_error(frappe.get_traceback(), f"Metrics aggregation failed for short {row.name}")

    @classmethod
    def refresh_short_totals(cls, short_id: str):
        flush_hot_metrics(limit=5000)
        values = {
            "like_count": cls._total_table_count("AOS Short Like", short_id),
            "comment_count": cls._total_table_count("AOS Short Comment", short_id, extra="AND status='active'"),
            "save_count": cls._total_table_count("AOS Short Save", short_id),
            "repost_count": cls._total_table_count("AOS Short Repost", short_id),
            "share_count": cls._total_event_count(short_id, "share"),
            "download_count": cls._total_event_count(short_id, "download"),
        }
        frappe.db.set_value("AOS Short", short_id, values, update_modified=False)

    @staticmethod
    def _event_count(short_id, day, event_type):
        row = frappe.db.sql(
            "SELECT COUNT(*) FROM `tabAOS Short Event` WHERE short=%s AND event_type=%s AND DATE(creation)=%s",
            (short_id, event_type, day),
        )
        return int(row[0][0] or 0)

    @staticmethod
    def _unique_viewers(short_id, day):
        row = frappe.db.sql(
            """SELECT COUNT(DISTINCT CASE WHEN user IS NOT NULL AND user!='' THEN CONCAT('u:',user) ELSE CONCAT('s:',session_id) END)
               FROM `tabAOS Short Event` WHERE short=%s AND event_type='qualified_view' AND DATE(creation)=%s""",
            (short_id, day),
        )
        return int(row[0][0] or 0)

    @staticmethod
    def _table_count(doctype, short_id, day, *, extra=""):
        row = frappe.db.sql(
            f"SELECT COUNT(*) FROM `tab{doctype}` WHERE short=%s AND DATE(creation)=%s {extra}",
            (short_id, day),
        )
        return int(row[0][0] or 0)

    @staticmethod
    def _total_table_count(doctype, short_id, *, extra=""):
        row = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{doctype}` WHERE short=%s {extra}", (short_id,))
        return int(row[0][0] or 0)

    @staticmethod
    def _total_event_count(short_id, event_type):
        row = frappe.db.sql("SELECT COUNT(*) FROM `tabAOS Short Event` WHERE short=%s AND event_type=%s", (short_id, event_type))
        return int(row[0][0] or 0)

    @classmethod
    def _upsert_metrics(
        cls, *, short_id, date, impressions, views, unique_viewers, watch_time_ms,
        avg_watch_time, completion_rate, rewatches, early_skips, likes, comments,
        shares, saves, downloads, reposts,
    ):
        values = {
            "impressions": impressions, "views": views, "unique_viewers": unique_viewers,
            "watch_time_ms": watch_time_ms, "avg_watch_time_ms": int(avg_watch_time),
            "completion_rate": float(completion_rate), "rewatches": rewatches,
            "early_skips": early_skips, "likes": likes, "comments": comments,
            "shares": shares, "saves": saves, "downloads": downloads, "reposts": reposts,
        }
        existing = frappe.db.get_value("AOS Short Metrics Daily", {"short": short_id, "date": date}, "name")
        if existing:
            frappe.db.set_value("AOS Short Metrics Daily", existing, values, update_modified=False)
        else:
            frappe.get_doc({"doctype": "AOS Short Metrics Daily", "short": short_id, "date": date, **values}).insert(ignore_permissions=True)
