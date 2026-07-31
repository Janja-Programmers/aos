"""Bounded Shorts persistence helpers."""

from __future__ import annotations

from typing import Any

import frappe


class ShortsRepository:
    def lock_short(self, short_id: str) -> dict[str, Any] | None:
        rows = frappe.db.sql(
            """
            SELECT name, owner, status, visibility_status, approval_status, audience,
                   allow_comments, allow_downloads, raw_video_media, duration_seconds
            FROM `tabAOS Short`
            WHERE name = %s
            LIMIT 1 FOR UPDATE
            """,
            (short_id,),
            as_dict=True,
        )
        return rows[0] if rows else None

    def lock_short_and_active_jobs(self, short_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        short = self.lock_short(short_id)
        jobs = frappe.db.sql(
            """
            SELECT name, status, creation, force_reprocess, idempotency_key, generation
            FROM `tabAOS Video Processing Job`
            WHERE short = %s AND status IN ('Queued', 'Dispatching', 'Processing')
            ORDER BY creation ASC, name ASC
            LIMIT 20 FOR UPDATE
            """,
            (short_id,),
            as_dict=True,
        )
        return short, jobs

    def increment_counter(self, short_id: str, field: str, delta: int) -> None:
        allowed = {
            "view_count", "like_count", "comment_count", "share_count", "save_count",
            "download_count", "repost_count", "impression_count",
        }
        if field not in allowed:
            raise ValueError("Unsupported Shorts counter")
        frappe.db.sql(
            f"UPDATE `tabAOS Short` SET `{field}` = GREATEST(0, COALESCE(`{field}`, 0) + %s) WHERE name = %s",
            (int(delta), short_id),
        )
