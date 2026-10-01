"""Install the current Shorts + Video Processing manual indexes.

This module is schema-only and idempotent so it can run both as a fresh-site
patch and from ``aos.migrate.after_migrate`` after DocType synchronization.
Normal tests call it explicitly when they need to assert manual-index
invariants; it never commits fixtures or application data.
"""
from __future__ import annotations

from collections.abc import Sequence

import frappe

# (doctype, index_name, columns, unique)
INDEXES: tuple[tuple[str, str, tuple[str, ...], bool], ...] = (
    ("AOS Short", "idx_short_feed", ("lifecycle_status", "moderation_status", "processing_status", "ranking_score", "posted_on", "name"), False),
    ("AOS Short", "idx_short_following", ("owner", "lifecycle_status", "moderation_status", "processing_status", "posted_on", "name"), False),
    ("AOS Short", "idx_short_creator", ("owner", "lifecycle_status", "creation", "name"), False),
    ("AOS Short", "idx_short_moderation_queue", ("moderation_status", "lifecycle_status", "modified", "name"), False),
    ("AOS Short", "idx_short_reuse_source", ("source_short", "reuse_type", "creation", "name"), False),
    ("AOS Short Photo", "uq_short_photo_position", ("short", "position"), True),
    ("AOS Short Photo", "uq_short_photo_media", ("short", "media"), True),
    ("AOS Short Mode", "uq_short_mode", ("short", "mode"), True),
    ("AOS Short Mode", "idx_short_mode_candidates", ("mode", "short"), False),
    ("AOS Short Hashtag", "uq_short_hashtag", ("short", "hashtag"), True),
    ("AOS Short Hashtag", "idx_short_hashtag_candidates", ("hashtag", "short"), False),
    ("AOS Short Ad", "uq_short_ad", ("short", "ad"), True),
    ("AOS Short Ad", "idx_short_ad_lookup", ("ad", "short"), False),
    ("AOS Short Mention", "uq_short_mention", ("short", "comment", "mentioned_account", "source_type"), True),
    ("AOS Short Mention", "idx_short_mention_target", ("mentioned_account", "creation", "short"), False),
    ("AOS Short Like", "uq_short_like", ("short", "user"), True),
    ("AOS Short Like", "idx_short_like_rollup", ("short", "creation"), False),
    ("AOS Short Like", "idx_short_like_user_recent", ("user", "creation", "short"), False),
    ("AOS Short Save", "uq_short_save", ("short", "user"), True),
    ("AOS Short Save", "idx_short_save_rollup", ("short", "creation"), False),
    ("AOS Short Save", "idx_short_save_user_recent", ("user", "creation", "short"), False),
    ("AOS Short Repost", "uq_short_repost", ("short", "user"), True),
    ("AOS Short Repost", "idx_short_repost_rollup", ("short", "creation"), False),
    ("AOS Short Repost", "idx_short_repost_user_recent", ("user", "creation", "short"), False),
    ("AOS Short Feedback", "uq_short_feedback", ("short", "user", "feedback_type"), True),
    ("AOS Short Feedback", "idx_short_feedback_user_recent", ("user", "creation", "short"), False),
    ("AOS Short Comment", "idx_short_comment_page", ("short", "status", "parent_comment", "creation", "name"), False),
    ("AOS Short Comment", "idx_short_comment_rollup", ("short", "status", "creation"), False),
    ("AOS Short Comment", "idx_short_comment_replies", ("root_comment", "status", "creation", "name"), False),
    ("AOS Short Comment", "idx_short_comment_author", ("user", "status", "creation", "short"), False),
    ("AOS Short Comment Like", "uq_short_comment_like", ("comment", "user"), True),
    ("AOS Short Sound", "uq_short_sound", ("short",), True),
    ("AOS Short Sound", "idx_short_sound_usage", ("sound", "short"), False),
    ("AOS Sound Favorite", "uq_sound_favorite", ("sound", "user"), True),
    ("AOS Sound Favorite", "idx_sound_favorite_user_recent", ("user", "creation", "sound"), False),
    ("AOS Sound", "idx_sound_catalog", ("status", "reuse_allowed", "usage_count", "creation", "name"), False),
    ("AOS Short View", "uq_short_view_identity_day", ("short", "view_date", "identity_key"), True),
    ("AOS Short View", "idx_short_view_user_recent", ("user", "last_seen_at", "short"), False),
    ("AOS Short View", "idx_short_view_session_recent", ("session_id", "last_seen_at", "short"), False),
    ("AOS Short Event", "idx_short_event_rollup", ("short", "event_type", "creation"), False),
    ("AOS Short Event", "idx_short_event_user_recent", ("user", "creation", "event_type", "short"), False),
    ("AOS Short Event", "idx_short_event_session_recent", ("session_id", "creation", "event_type", "short"), False),
    ("AOS Short Moderation Decision", "idx_short_moderation_history", ("short", "revision", "generation", "creation"), False),
    ("AOS Video Processing Job", "uq_short_processing_active", ("active_key",), True),
    ("AOS Video Processing Job", "idx_short_job_lifecycle", ("short", "operation", "status", "generation", "creation"), False),
    ("AOS Video Processing Job", "idx_short_job_retry", ("status", "next_retry_at", "name"), False),
    ("AOS Video Processing Job", "idx_short_job_lease", ("status", "lease_expires_at", "name"), False),
    ("AOS Short Metrics Daily", "uq_short_metrics_day", ("short", "date"), True),
)


def execute() -> None:
    for doctype, index_name, columns, unique in INDEXES:
        _ensure_index(doctype, index_name, columns, unique=unique)


def _table(doctype: str) -> str:
    return f"tab{doctype}"


def _index_exists(doctype: str, index_name: str) -> bool:
    return bool(
        frappe.db.sql(
            """SELECT 1 FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
                LIMIT 1""",
            (_table(doctype), index_name),
        )
    )


def _ensure_columns(doctype: str, columns: Sequence[str]) -> None:
    if not frappe.db.table_exists(doctype):
        frappe.throw(f"Shorts schema table is missing: {doctype}")
    missing = [column for column in columns if not frappe.db.has_column(doctype, column)]
    if missing:
        frappe.throw(f"Shorts schema columns are missing for {doctype}: {', '.join(missing)}")


def _assert_unique_ready(doctype: str, columns: Sequence[str], index_name: str) -> None:
    # NULL active keys intentionally do not conflict.  For composite indexes,
    # null-bearing rows likewise do not violate the invariant.
    non_null = " AND ".join(f"`{column}` IS NOT NULL" for column in columns)
    group_by = ", ".join(f"`{column}`" for column in columns)
    duplicate = frappe.db.sql(
        f"""SELECT 1 FROM `{_table(doctype)}` WHERE {non_null}
             GROUP BY {group_by} HAVING COUNT(*)>1 LIMIT 1"""
    )
    if duplicate:
        frappe.throw(f"Cannot install Shorts unique index {index_name}; duplicate data remains in {doctype}")


def _ensure_index(doctype: str, index_name: str, columns: tuple[str, ...], *, unique: bool) -> None:
    _ensure_columns(doctype, columns)
    if _index_exists(doctype, index_name):
        return
    if unique:
        _assert_unique_ready(doctype, columns, index_name)
        frappe.db.add_unique(doctype, list(columns), constraint_name=index_name)
    else:
        frappe.db.add_index(doctype, list(columns), index_name=index_name)
