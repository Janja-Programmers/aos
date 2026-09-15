"""Saved Search API transaction/error boundary."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import frappe

from aos.api.shared.responses import fail
from aos.services.ads.api import ads_fail
from aos.services.ads.errors import AdsError


def run_saved_search_mutation(
    operation: Callable[[], dict[str, Any]], *, fallback: str, log_title: str
) -> dict[str, Any]:
    """Protect a caught mutation with a request-local savepoint."""
    savepoint = f"aos_saved_search_{frappe.generate_hash(length=10)}"
    frappe.db.savepoint(savepoint)
    try:
        response = operation()
        if isinstance(response, dict) and not response.get("ok"):
            frappe.db.rollback(save_point=savepoint)
        return response
    except AdsError as exc:
        frappe.db.rollback(save_point=savepoint)
        return ads_fail(exc, fallback=fallback)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), log_title)
        return fail(fallback, error="INTERNAL_ERROR", http_status=500)
