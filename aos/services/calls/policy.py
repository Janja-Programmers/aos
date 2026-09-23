"""Central Calls authorization and concurrency policy for direct/group calls."""
from __future__ import annotations

import frappe

from aos.api.shared.account_status import ensure_account_active
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.responses import fail

from .participants import participant_for_user, users_for_call

ACTIVE_STATUSES = ("initiated", "ringing", "ongoing")


def ensure_user_available(user: str, *, target: bool = False):
    user = str(user or "").strip()
    if not user:
        return fail("Account unavailable.", error="ACCOUNT_DISABLED", http_status=404 if target else 403)
    row = frappe.db.get_value("User", user, ["name", "enabled"], as_dict=True)
    if not row or int(row.enabled or 0) != 1 or not frappe.db.exists("AOS Profile", {"user": user}):
        return fail("Account unavailable.", error="ACCOUNT_DISABLED", http_status=404 if target else 403)
    state_error = ensure_account_active(user)
    if state_error:
        return fail("Account unavailable.", error="ACCOUNT_DISABLED", http_status=404) if target else state_error
    return None


def ensure_interaction_allowed(*, current_user: str, peer_user: str, action: str = "call"):
    actor_error = ensure_user_available(current_user, target=False)
    if actor_error:
        return actor_error
    peer_error = ensure_user_available(peer_user, target=True)
    if peer_error:
        return peer_error
    return ensure_not_blocked(current_user=current_user, target_user=peer_user, action=action)


def ensure_call_interaction_allowed(call, current_user: str, *, action: str = "call"):
    participant = participant_for_user(call.name, current_user)
    if not participant:
        return fail("Not allowed.", error="PERMISSION_DENIED", http_status=403)
    actor_error = ensure_user_available(current_user, target=False)
    if actor_error:
        return actor_error
    # Direct calls continuously enforce the peer relationship. For groups, an
    # invitation is authorized against the account that actually added this
    # participant. Once joined, membership is durable and the inviter/initiator
    # is not the conference lifetime owner.
    if call.call_mode == "direct" and current_user != call.initiator:
        return ensure_interaction_allowed(current_user=current_user, peer_user=call.initiator, action=action)
    if (
        call.call_mode == "group"
        and participant.status in {"invited", "ringing"}
        and participant.added_by
        and participant.added_by != current_user
    ):
        return ensure_interaction_allowed(
            current_user=current_user, peer_user=participant.added_by, action=action
        )
    return None


def lock_users_for_call(*users: str) -> None:
    normalized = tuple(sorted({str(user or "").strip() for user in users if str(user or "").strip()}))
    if not normalized:
        return
    frappe.db.sql(
        "SELECT name FROM `tabAOS Profile` WHERE user IN %(users)s ORDER BY user, name FOR UPDATE",
        {"users": normalized},
    )
    frappe.db.sql(
        "SELECT name FROM `tabUser` WHERE name IN %(users)s ORDER BY name FOR UPDATE",
        {"users": normalized},
    )


def active_call_names_for_users(*users: str) -> list[str]:
    normalized = tuple(sorted({str(user or "").strip() for user in users if str(user or "").strip()}))
    if not normalized:
        return []
    rows = frappe.db.sql(
        """
        SELECT DISTINCT c.name
        FROM `tabAOS Call` c
        INNER JOIN `tabAOS Call Participant` p ON p.`call`=c.name
        WHERE c.is_active=1
          AND c.status IN ('initiated','ringing','ongoing')
          AND p.status IN ('invited','ringing','joined')
          AND p.user IN %(users)s
        ORDER BY c.creation DESC, c.name DESC
        LIMIT 10
        """,
        {"users": normalized},
        pluck=True,
    )
    return [str(name) for name in rows]


def active_call_for_users(*users: str):
    names = active_call_names_for_users(*users)
    if not names:
        return None
    return frappe.db.get_value(
        "AOS Call",
        names[0],
        ["name", "public_id", "conversation", "initiator", "call_mode", "call_type", "status", "is_active", "room_name", "state_version", "rtc_provisioned_at", "max_participants", "participant_count"],
        as_dict=True,
    )


def lock_call_row(call_id: str) -> None:
    call_id = str(call_id or "").strip()
    if call_id:
        frappe.db.sql("SELECT name FROM `tabAOS Call` WHERE name=%s LIMIT 1 FOR UPDATE", (call_id,))


def lock_call_participants(call_name: str) -> list[str]:
    users = users_for_call(call_name)
    lock_users_for_call(*users)
    lock_call_row(call_name)
    frappe.db.sql(
        "SELECT name FROM `tabAOS Call Participant` WHERE `call`=%s ORDER BY user,name FOR UPDATE",
        (call_name,),
    )
    return users
