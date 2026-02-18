"""Change an Ad's status (seller actions).

This module centralizes lifecycle transitions so the mobile app can call one
endpoint and the server remains the single source of truth for what is allowed.

Supported actions (action -> transition):
- mark_sold:       Active  -> Sold
- mark_available:  Sold    -> Active
- renew:           Expired -> Active (extends expires_on)
- delete:          Reviewing/Declined/Sold/Expired -> Deleted

Notes:
- "delete" is a soft delete via status="Deleted".
- Editing while Reviewing should restart review (handled in update endpoint).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

import frappe
from frappe.utils import add_days, today

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from aos.utils.aos_settings import get_aos_settings_snapshot
from .constants import SET_AD_STATUS_LIMIT_PER_MINUTE_PER_USER

_ACTIONS = {
    "mark_sold",
    "mark_available",
    "renew",
    "delete",
}

_DELETABLE_STATUSES = {"Reviewing", "Declined", "Sold", "Expired"}


def _clean_str(val: Any) -> str:
    return str(val or "").strip()


def _get_transition(action: str, status: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (new_status, error_message) for the requested action."""
    action = _clean_str(action).lower()
    status = _clean_str(status)

    if action not in _ACTIONS:
        return None, "Invalid action."

    if status == "Deleted":
        return None, "This ad is deleted."

    if action == "mark_sold":
        if status != "Active":
            return None, "Only Active ads can be marked as Sold."
        return "Sold", None

    if action == "mark_available":
        if status != "Sold":
            return None, "Only Sold ads can be marked as Available."
        return "Active", None

    if action == "renew":
        if status != "Expired":
            return None, "Only Expired ads can be renewed."
        return "Active", None

    # action == "delete"
    if status not in _DELETABLE_STATUSES:
        return None, "This ad cannot be deleted in its current status."
    return "Deleted", None


def set_ad_status_impl(**kwargs):
    user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:ads:status:user:{user}",
        ttl_seconds=60,
        limit=SET_AD_STATUS_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    ad_id = _clean_str(kwargs.get("ad_id") or kwargs.get("id"))
    if not ad_id:
        return fail("Ad id is required.", code="VALIDATION_ERROR")

    action = _clean_str(kwargs.get("action")).lower()
    if not action:
        return fail("Action is required.", code="VALIDATION_ERROR")

    # Fetch minimal info first for ownership + status checks.
    row = frappe.db.get_value(
        "AOS Ad",
        ad_id,
        ["name", "user", "status", "expires_on"],
        as_dict=True,
    )
    if not row:
        return fail("Ad not found.", code="NOT_FOUND")

    if _clean_str(row.user) != user:
        return fail("You don't have permission to change this ad.", code="FORBIDDEN")

    current_status = _clean_str(row.status)
    new_status, msg = _get_transition(action, current_status)
    if msg:
        return fail(msg, code="VALIDATION_ERROR")

    try:
        doc = frappe.get_doc("AOS Ad", ad_id)

        # Apply transition
        doc.status = new_status

        if action == "renew" and new_status == "Active":
            # Extend expiry window from today using configured AOS Settings.ad_expiry_days.
            settings = get_aos_settings_snapshot()
            doc.expires_on = add_days(today(), settings.ad_expiry_days)

        doc.save(ignore_permissions=True)
        frappe.db.commit()

        return ok(
            "Ad status updated.",
            data={
                "id": doc.name,
                "status": doc.status,
                "expires_on": getattr(doc, "expires_on", None),
            },
        )

    except frappe.DoesNotExistError:
        return fail("Ad not found.", code="NOT_FOUND")
    except frappe.ValidationError as ex:
        return fail(str(ex), code="VALIDATION_ERROR")
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Set Ad Status Failed")
        return fail("Failed to update ad status.", code="INTERNAL_ERROR")
