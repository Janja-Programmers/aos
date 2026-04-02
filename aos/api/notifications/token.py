"""
Push Token APIs (implementation).

Handles:
- register_push_token
- deactivate_push_token
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import ok, fail

from .constants import (
    REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
    DEACTIVATE_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
)


# REGISTER TOKEN
def register_push_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:push:register:user:{current_user}",
        ttl_seconds=60,
        limit=REGISTER_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    token = (kwargs.get("token") or "").strip()
    device_type = (kwargs.get("device_type") or "").strip()
    device_id = (kwargs.get("device_id") or "").strip()

    if not token:
        return fail("token is required.", code="VALIDATION_ERROR")

    if not device_type:
        return fail("device_type is required.", code="VALIDATION_ERROR")

    try:
        now = now_datetime()

        existing = frappe.db.get_value(
            "AOS Push Token",
            {"token": token},
            ["name"],
        )

        if existing:
            # Update existing token
            frappe.db.set_value(
                "AOS Push Token",
                existing,
                {
                    "user": current_user,
                    "device_type": device_type,
                    "device_id": device_id,
                    "is_active": 1,
                    "last_used_at": now,
                },
                update_modified=False,
            )

            return ok("Push token updated.")

        # Create new token
        doc = frappe.get_doc(
            {
                "doctype": "AOS Push Token",
                "user": current_user,
                "token": token,
                "device_type": device_type,
                "device_id": device_id,
                "is_active": 1,
                "last_used_at": now,
            }
        )

        doc.insert(ignore_permissions=True)

        return ok("Push token registered.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Register Push Token Failed",
        )
        frappe.db.rollback()
        return fail("Failed to register push token.", code="INTERNAL_ERROR")


# DEACTIVATE TOKEN
def deactivate_push_token_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:push:deactivate:user:{current_user}",
        ttl_seconds=60,
        limit=DEACTIVATE_PUSH_TOKEN_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    token = (kwargs.get("token") or "").strip()

    if not token:
        return fail("token is required.", code="VALIDATION_ERROR")

    try:
        exists = frappe.db.exists(
            "AOS Push Token",
            {"token": token, "user": current_user},
        )

        if not exists:
            return ok("Token not found or already inactive.")

        frappe.db.set_value(
            "AOS Push Token",
            {"token": token},
            {
                "is_active": 0,
                "last_used_at": now_datetime(),
            },
            update_modified=False,
        )

        return ok("Push token deactivated.")

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Deactivate Push Token Failed",
        )
        frappe.db.rollback()
        return fail("Failed to deactivate push token.", code="INTERNAL_ERROR")
