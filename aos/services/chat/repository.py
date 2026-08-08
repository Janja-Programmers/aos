"""Small deterministic-lock helpers for Chat mutations.

Lock ordering is always conversation rows first (sorted by public conversation
ID), then message rows (sorted by public message ID). Feature code can fetch
richer projections after these locks are held without re-implementing lock
order and risking deadlocks between edit/delete/forward/reaction/star paths.
"""

from __future__ import annotations

from collections.abc import Iterable

import frappe


def _unique(values: Iterable[str]) -> list[str]:
    return sorted({str(value).strip() for value in values if str(value or "").strip()})


def lock_conversations(conversation_ids: Iterable[str]) -> list[str]:
    ids = _unique(conversation_ids)
    if not ids:
        return []
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Conversation`
        WHERE name IN %(ids)s
        ORDER BY name ASC
        FOR UPDATE
        """,
        {"ids": tuple(ids)},
        pluck=True,
    )
    return list(rows or [])


def lock_messages(message_ids: Iterable[str]) -> list[str]:
    ids = _unique(message_ids)
    if not ids:
        return []
    rows = frappe.db.sql(
        """
        SELECT name
        FROM `tabAOS Message`
        WHERE name IN %(ids)s
        ORDER BY name ASC
        FOR UPDATE
        """,
        {"ids": tuple(ids)},
        pluck=True,
    )
    return list(rows or [])
