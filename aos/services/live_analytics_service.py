"""
Live Analytics Service for AOS

Responsibilities:
- Maintain lightweight counters on AOS Live Stream
- Handle comments and reactions

IMPORTANT:
- Viewer count is NOT handled here
- Viewer count is derived from AOS Live Stream View (source of truth)
"""

from __future__ import annotations

import frappe


class LiveAnalyticsService:
    # COMMENT EVENTS
    @classmethod
    def handle_comment_added(cls, *, live_id: str):
        """Increment comment count."""
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
        """Decrement comment count safely."""
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

    # REACTION EVENTS
    @classmethod
    def handle_reaction(cls, *, live_id: str):
        """Increment reaction count."""
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

    # OPTIONAL: RESET / SYNC HELPERS
    @classmethod
    def sync_comment_count(cls, *, live_id: str):
        """
        Recalculate comment count from source of truth.
        Useful for repair jobs or scheduled consistency checks.
        """
        try:
            result = frappe.db.sql(
                """
                SELECT COUNT(*) as count
                FROM `tabAOS Live Stream Comment`
                WHERE live_stream = %s AND is_deleted = 0
                """,
                (live_id,),
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

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_comment_count failed for live {live_id}",
            )

    @classmethod
    def sync_reaction_count(cls, *, live_id: str):
        """
        Recalculate reaction count from source of truth.
        Useful for repair jobs or scheduled consistency checks.
        """
        try:
            result = frappe.db.sql(
                """
                SELECT COUNT(*) as count
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

        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"sync_reaction_count failed for live {live_id}",
            )
