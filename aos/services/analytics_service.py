from __future__ import annotations

from datetime import timedelta

import frappe
from frappe.utils import getdate

from aos.services.shorts.hot_metrics import daily_snapshot


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
        # The task flushes the bounded dirty set once per batch, not once per row.
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
    def _day_bounds(day):
        # Half-open ranges preserve the MariaDB creation-index access path.
        return f"{day} 00:00:00", f"{day + timedelta(days=1)} 00:00:00"

    @classmethod
    def _event_count(cls, short_id, day, event_type):
        start, end = cls._day_bounds(day)
        row = frappe.db.sql(
            """SELECT COUNT(*) FROM `tabAOS Short Event`
               WHERE short=%s AND event_type=%s AND creation >= %s AND creation < %s""",
            (short_id, event_type, start, end),
        )
        return int(row[0][0] or 0)

    @classmethod
    def _unique_viewers(cls, short_id, day):
        start, end = cls._day_bounds(day)
        row = frappe.db.sql(
            """SELECT COUNT(DISTINCT CASE WHEN user IS NOT NULL AND user!='' THEN CONCAT('u:',user) ELSE CONCAT('s:',session_id) END)
               FROM `tabAOS Short Event`
               WHERE short=%s AND event_type='qualified_view' AND creation >= %s AND creation < %s""",
            (short_id, start, end),
        )
        return int(row[0][0] or 0)

    @classmethod
    def _table_count(cls, doctype, short_id, day, *, extra=""):
        if doctype not in {"AOS Short Like", "AOS Short Comment", "AOS Short Save", "AOS Short Repost"}:
            raise ValueError("Unsupported Short metrics source")
        if extra not in {"", "AND status='active'"} or (extra and doctype != "AOS Short Comment"):
            raise ValueError("Unsupported Short metrics predicate")
        start, end = cls._day_bounds(day)
        row = frappe.db.sql(
            f"SELECT COUNT(*) FROM `tab{doctype}` WHERE short=%s AND creation >= %s AND creation < %s {extra}",
            (short_id, start, end),
        )
        return int(row[0][0] or 0)

    @staticmethod
    def _total_table_count(doctype, short_id, *, extra=""):
        if doctype not in {"AOS Short Like", "AOS Short Comment", "AOS Short Save", "AOS Short Repost"}:
            raise ValueError("Unsupported Short metrics source")
        if extra not in {"", "AND status='active'"} or (extra and doctype != "AOS Short Comment"):
            raise ValueError("Unsupported Short metrics predicate")
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
        key = {"short": short_id, "date": date}
        existing = frappe.db.get_value("AOS Short Metrics Daily", key, "name")
        if existing:
            frappe.db.set_value("AOS Short Metrics Daily", existing, values, update_modified=False)
            return
        try:
            frappe.get_doc({"doctype": "AOS Short Metrics Daily", **key, **values}).insert(ignore_permissions=True)
        except frappe.DuplicateEntryError:
            # Another worker inserted the same (short, date) after our lookup.
            # The unique index owns correctness; retry only the exact day row.
            existing = frappe.db.get_value("AOS Short Metrics Daily", key, "name")
            if not existing:
                raise
            frappe.db.set_value("AOS Short Metrics Daily", existing, values, update_modified=False)
