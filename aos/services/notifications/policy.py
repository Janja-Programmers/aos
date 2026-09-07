"""Canonical Notification delivery privacy/account policy.

Business domains remain authoritative. These helpers only decide whether an
already-modeled Notification transport may expose an event to its recipient.
"""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import get_account_state
from aos.api.shared.blocking import is_blocked_between
from aos.services.notifications.contracts import NotificationContractError, contract_for


def account_availability_reason(user: str) -> str | None:
    user = str(user or "").strip()
    if not user or not frappe.db.exists("User", user):
        return "missing"
    try:
        enabled = frappe.db.get_value("User", user, "enabled")
        if enabled is not None and not bool(int(enabled or 0)):
            return "disabled"
    except Exception:
        return "unavailable"
    try:
        state = get_account_state(user)
    except Exception:
        return "unavailable"
    if state.get("exists") and (
        state.get("is_deleted")
        or state.get("is_suspended")
    ):
        return "inactive"
    return None


def relationship_suppression_reason(user: str, actor: str) -> str | None:
    try:
        if is_blocked_between(user, actor):
            return "blocked_relationship"
    except Exception:
        # Privacy policy uncertainty fails closed for delivery only.
        return "relationship_unavailable"
    return None


def persistent_notification_suppression_reason(
    *, user: str, notification_type: str, actor: str | None
) -> str | None:
    recipient_reason = account_availability_reason(user)
    if recipient_reason:
        return f"recipient_{recipient_reason}"
    actor = str(actor or "").strip() or None
    if actor and actor == user:
        return "self_notification"
    try:
        contract = contract_for(notification_type)
    except NotificationContractError:
        return "notification_contract_mismatch"
    if actor and contract.actor_scoped:
        actor_reason = account_availability_reason(actor)
        if actor_reason:
            return "actor_unavailable"
        return relationship_suppression_reason(user, actor)
    return None


def transient_recipient_suppression_reason(*, user: str, actor: str | None) -> str | None:
    recipient_reason = account_availability_reason(user)
    if recipient_reason:
        return f"recipient_{recipient_reason}"
    actor = str(actor or "").strip() or None
    if actor and actor == user:
        return "self_notification"
    return None
