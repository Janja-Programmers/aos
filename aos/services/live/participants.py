"""Durable participant consistency intents for AOS Live."""

from __future__ import annotations

import frappe

from .observability import live_log


def _enqueue(method: str, **kwargs) -> None:
    try:
        frappe.enqueue(
            method,
            queue="short",
            enqueue_after_commit=True,
            **kwargs,
        )
    except Exception:
        live_log("participant_removal_enqueue", outcome="failure", reason="dependency")


def enqueue_cohost_removal(cohost_id: str) -> None:
    """Remove/revoke a co-host participant after the enclosing transaction commits."""
    if cohost_id:
        _enqueue("aos.tasks.live.remove_live_cohost_participant", cohost_id=cohost_id)


def enqueue_view_removal(view_id: str) -> None:
    """Remove/revoke a tracked viewer after the enclosing transaction commits."""
    if view_id:
        _enqueue("aos.tasks.live.remove_live_view_participant", view_id=view_id)
