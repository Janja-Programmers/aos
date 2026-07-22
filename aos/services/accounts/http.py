"""HTTP cache policy helpers for Accounts responses."""

from __future__ import annotations

import frappe

from .constants import PRIVATE_CACHE_CONTROL, PUBLIC_CACHE_CONTROL


def set_private_no_store() -> None:
    try:
        frappe.local.response.headers["Cache-Control"] = PRIVATE_CACHE_CONTROL
        frappe.local.response.headers["Pragma"] = "no-cache"
        frappe.local.response.headers["Expires"] = "0"
    except Exception:
        pass


def set_public_cache() -> None:
    try:
        frappe.local.response.headers["Cache-Control"] = PUBLIC_CACHE_CONTROL
    except Exception:
        pass
