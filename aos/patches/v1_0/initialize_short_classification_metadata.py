"""Initialize automatic-classification metadata without changing valid legacy modes."""

from __future__ import annotations

import frappe


_VALID_MODES = ("shop", "geo", "vibes", "learn")


def execute() -> None:
    """Mark existing publishable Shorts as legacy-classified.

    The patch is idempotent, owns no commit, and updates only rows that have not
    yet received automatic classification metadata. Existing valid mode choices
    are preserved; missing/invalid historical values receive the safe Vibes
    fallback so they remain eligible for All and one canonical mode feed.
    """
    placeholders = ", ".join(["%s"] * len(_VALID_MODES))
    frappe.db.sql(
        f"""
        UPDATE `tabAOS Short`
        SET
            classification_status = CASE
                WHEN content_mode IN ({placeholders}) THEN 'legacy'
                ELSE 'fallback'
            END,
            classification_source = CASE
                WHEN content_mode IN ({placeholders}) THEN 'legacy'
                ELSE 'fallback'
            END,
            classification_confidence = CASE
                WHEN content_mode IN ({placeholders}) THEN 1.0
                ELSE 0.0
            END,
            classification_model = 'legacy-content-mode',
            classification_model_version = '1',
            classified_at = COALESCE(posted_on, modified),
            content_mode = CASE
                WHEN content_mode IN ({placeholders}) THEN content_mode
                ELSE 'vibes'
            END
        WHERE COALESCE(classification_status, '') IN ('', 'pending')
          AND (status = 'ready' OR posted_on IS NOT NULL OR visibility_status = 'visible')
        """,
        (*_VALID_MODES, *_VALID_MODES, *_VALID_MODES, *_VALID_MODES),
    )
