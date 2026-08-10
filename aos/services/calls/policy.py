"""Central Calls authorization and concurrency policy."""

from __future__ import annotations

import frappe

from aos.api.shared.account_status import ensure_account_active
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.responses import fail

ACTIVE_STATUSES = ("initiated", "ringing", "ongoing")


def ensure_user_available(user: str, *, target: bool = False):
    user = str(user or "").strip()
    if not user:
        return fail("Account unavailable.", error="ACCOUNT_DISABLED", http_status=404 if target else 403)
    row = frappe.db.get_value("User", user, ["name", "enabled"], as_dict=True)
    if not row or int(row.enabled or 0) != 1 or not frappe.db.exists("AOS Profile", user):
        return fail(
            "Account unavailable.",
            error="ACCOUNT_DISABLED",
            http_status=404 if target else 403,
        )
    state_error = ensure_account_active(user)
    if state_error:
        if target:
            return fail("Account unavailable.", error="ACCOUNT_DISABLED", http_status=404)
        return state_error
    return None


def ensure_interaction_allowed(*, current_user: str, peer_user: str, action: str = "call"):
    actor_error = ensure_user_available(current_user, target=False)
    if actor_error:
        return actor_error
    peer_error = ensure_user_available(peer_user, target=True)
    if peer_error:
        return peer_error
    return ensure_not_blocked(current_user=current_user, target_user=peer_user, action=action)


def peer_for_call(call, current_user: str) -> str | None:
    if current_user == call.caller:
        return call.receiver
    if current_user == call.receiver:
        return call.caller
    return None


def ensure_call_interaction_allowed(call, current_user: str, *, action: str = "call"):
    peer = peer_for_call(call, current_user)
    if not peer:
        return fail("Not allowed.", error="PERMISSION_DENIED", http_status=403)
    return ensure_interaction_allowed(current_user=current_user, peer_user=peer, action=action)


def lock_users_for_call(*users: str) -> None:
    normalized = tuple(sorted({str(user or "").strip() for user in users if str(user or "").strip()}))
    if not normalized:
        return
    # Accounts lifecycle locks Profile before updating User. Use the same order
    # to avoid Profile<->User deadlocks; Social block writes serialize on User.
    frappe.db.sql(
        """
        SELECT name FROM `tabAOS Profile`
        WHERE user IN %(users)s
        ORDER BY user, name
        FOR UPDATE
        """,
        {"users": normalized},
    )
    frappe.db.sql(
        """
        SELECT name FROM `tabUser`
        WHERE name IN %(users)s
        ORDER BY name
        FOR UPDATE
        """,
        {"users": normalized},
    )


def active_call_for_users(*users: str):
    normalized = tuple(sorted({str(user or "").strip() for user in users if str(user or "").strip()}))
    if not normalized:
        return None
    rows = frappe.db.sql(
        """
        SELECT name, conversation, caller, receiver, call_type, status, is_active, room_name
        FROM `tabAOS Call`
        WHERE is_active = 1
          AND status IN ('initiated', 'ringing', 'ongoing')
          AND (caller IN %(users)s OR receiver IN %(users)s)
        ORDER BY creation DESC, name DESC
        LIMIT 1
        """,
        {"users": normalized},
        as_dict=True,
    )
    return rows[0] if rows else None


def lock_call_row(call_id: str) -> None:
    call_id = str(call_id or "").strip()
    if not call_id:
        return
    frappe.db.sql(
        "SELECT name FROM `tabAOS Call` WHERE name = %s LIMIT 1 FOR UPDATE",
        (call_id,),
    )
