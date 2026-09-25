"""Transaction-safe best-effort boundary for Activity producer hooks."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

import frappe

from .observability import activity_log


def best_effort_activity(label: str, fn: Callable[..., Any], /, *args, **kwargs):
    """Run one optional Activity mutation behind a caller-owned savepoint.

    Activity deliberately never commits. If its projection write fails, only
    Activity work is rolled back and the authoritative producer transaction can
    continue. If the producer transaction later rolls back, Activity rolls back
    with it, preventing phantom history.
    """
    savepoint = f"aos_activity_hook_{uuid.uuid4().hex[:12]}"
    try:
        frappe.db.savepoint(savepoint)
    except Exception:
        activity_log("activity.producer", outcome="failure")
        return None
    try:
        return fn(*args, **kwargs)
    except Exception:
        try:
            frappe.db.rollback(save_point=savepoint)
        except Exception:
            pass
        activity_log("activity.producer", outcome="failure")
        frappe.log_error(
            "Activity projection hook failed.",
            f"AOS Activity Hook Failed: {str(label or 'producer')[:80]}",
        )
        return None
