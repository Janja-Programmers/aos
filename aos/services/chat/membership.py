"""Canonical conversation membership primitives.

All Chat authorization goes through AOS Conversation Participant. Conversation rows
contain shared metadata only; user-specific inbox/read/lock state belongs to the
participant row.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.accounts.identity import public_account_id_for_user
from aos.services.chat.errors import ChatError

DIRECT = "direct"
GROUP = "group"
ACTIVE = "active"
LEFT = "left"
REMOVED = "removed"
ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
GROUP_MANAGERS = frozenset({ROLE_OWNER, ROLE_ADMIN})
MAX_GROUP_PARTICIPANTS = 256
MIN_GROUP_PARTICIPANTS = 2


def direct_key(user_a: str, user_b: str) -> str:
    users = sorted((str(user_a).strip(), str(user_b).strip()))
    if not all(users) or users[0] == users[1]:
        raise ChatError("Invalid direct conversation participants.", code="CHAT_INVALID_REQUEST", http_status=422)
    return hashlib.sha256("\x1f".join(users).encode("utf-8")).hexdigest()


def get_conversation(conversation_id: str, *, for_update: bool = False):
    sql = """
        SELECT name, conversation_type, direct_key, title, avatar_media, created_by,
               participant_count, membership_version, last_message, last_message_at, last_sender,
               creation, modified
        FROM `tabAOS Conversation`
        WHERE name=%s
        LIMIT 1
    """
    if for_update:
        sql += " FOR UPDATE"
    rows = frappe.db.sql(sql, (conversation_id,), as_dict=True)
    return rows[0] if rows else None


def get_membership(conversation_id: str, user: str, *, for_update: bool = False, include_inactive: bool = True):
    sql = """
        SELECT name, conversation, user, role, status, joined_at, visible_from, left_at, added_by,
               unread_count, is_hidden, cleared_before, last_visible_message,
               last_visible_message_at, last_visible_sender, is_locked, lock_changed_at
        FROM `tabAOS Conversation Participant`
        WHERE conversation=%s AND user=%s
        LIMIT 1
    """
    if for_update:
        sql += " FOR UPDATE"
    rows = frappe.db.sql(sql, (conversation_id, user), as_dict=True)
    row = rows[0] if rows else None
    if row and not include_inactive and row.status != ACTIVE:
        return None
    return row


def require_active_membership(conversation_id: str, user: str, *, for_update: bool = False):
    conversation = get_conversation(conversation_id, for_update=for_update)
    if not conversation:
        raise ChatError("Conversation not found.", code="CHAT_NOT_FOUND", http_status=404)
    membership = get_membership(conversation_id, user, for_update=for_update, include_inactive=False)
    if not membership:
        # Fail closed as not-found so knowing an ID does not reveal membership/history.
        raise ChatError("Conversation not found.", code="CHAT_NOT_FOUND", http_status=404)
    return conversation, membership


def active_members(conversation_id: str, *, limit: int = MAX_GROUP_PARTICIPANTS) -> list[Any]:
    return frappe.get_all(
        "AOS Conversation Participant",
        filters={"conversation": conversation_id, "status": ACTIVE},
        fields=["name", "user", "role", "joined_at", "visible_from", "unread_count", "is_hidden", "is_locked"],
        order_by="joined_at asc, name asc",
        limit=max(1, min(int(limit), MAX_GROUP_PARTICIPANTS)),
    )


def active_member_users(conversation_id: str, *, exclude_user: str | None = None) -> list[str]:
    rows = active_members(conversation_id)
    result = [str(row.user) for row in rows if row.user and row.user != exclude_user]
    return result


def membership_public(row: Any) -> dict[str, Any]:
    return {
        "account_id": public_account_id_for_user(row.user),
        "role": str(row.role),
        "status": str(row.status),
        "joined_at": row.joined_at,
    }


def increment_membership_version(conversation_id: str) -> None:
    frappe.db.sql(
        "UPDATE `tabAOS Conversation` SET membership_version=COALESCE(membership_version,0)+1 WHERE name=%s",
        (conversation_id,),
    )


def refresh_participant_count(conversation_id: str) -> int:
    count = int(
        frappe.db.count("AOS Conversation Participant", {"conversation": conversation_id, "status": ACTIVE}) or 0
    )
    frappe.db.set_value("AOS Conversation", conversation_id, "participant_count", count, update_modified=False)
    return count


def create_membership(
    *,
    conversation_id: str,
    user: str,
    role: str,
    added_by: str,
    visible_from=None,
):
    now = now_datetime()
    existing = get_membership(conversation_id, user, for_update=True, include_inactive=True)
    if existing:
        if existing.status == ACTIVE:
            return frappe.get_doc("AOS Conversation Participant", existing.name)
        frappe.db.set_value(
            "AOS Conversation Participant",
            existing.name,
            {
                "role": role,
                "status": ACTIVE,
                "joined_at": now,
                "visible_from": visible_from or now,
                "left_at": None,
                "added_by": added_by,
                "unread_count": 0,
                "is_hidden": 0,
                "cleared_before": None,
                "last_visible_message": None,
                "last_visible_message_at": None,
                "last_visible_sender": None,
            },
            update_modified=True,
        )
        return frappe.get_doc("AOS Conversation Participant", existing.name)
    doc = frappe.new_doc("AOS Conversation Participant")
    doc.conversation = conversation_id
    doc.user = user
    doc.role = role
    doc.status = ACTIVE
    doc.joined_at = now
    doc.visible_from = visible_from or now
    doc.added_by = added_by
    doc.insert(ignore_permissions=True)
    return doc


def mark_membership_inactive(*, membership_name: str, status: str) -> None:
    if status not in {LEFT, REMOVED}:
        raise ValueError("invalid inactive membership status")
    frappe.db.set_value(
        "AOS Conversation Participant",
        membership_name,
        {
            "status": status,
            "left_at": now_datetime(),
            "unread_count": 0,
            "is_hidden": 1,
        },
        update_modified=True,
    )


def lock_memberships(conversation_id: str, users: Iterable[str] | None = None) -> list[str]:
    params: dict[str, Any] = {"conversation": conversation_id}
    condition = "conversation=%(conversation)s"
    clean_users = sorted({str(u).strip() for u in (users or []) if str(u or "").strip()})
    if clean_users:
        condition += " AND user IN %(users)s"
        params["users"] = tuple(clean_users)
    rows = frappe.db.sql(
        f"""
        SELECT name FROM `tabAOS Conversation Participant`
        WHERE {condition}
        ORDER BY user ASC, name ASC
        FOR UPDATE
        """,
        params,
        pluck=True,
    )
    return list(rows or [])


def assert_group_manager(conversation: Any, membership: Any) -> None:
    if conversation.conversation_type != GROUP or membership.role not in GROUP_MANAGERS:
        raise ChatError("Chat action is not allowed.", code="CHAT_ACCESS_DENIED", http_status=403)


def transfer_owner_if_needed(conversation_id: str, departing_user: str) -> str | None:
    """Ensure an active group always has at most one owner.

    If the owner leaves/is removed, ownership moves deterministically to the oldest
    active admin, otherwise the oldest active member. No Python/local lock is used;
    caller holds the conversation + membership locks.
    """
    departing = get_membership(conversation_id, departing_user, include_inactive=True)
    if not departing or departing.role != ROLE_OWNER:
        return None
    rows = frappe.db.sql(
        """
        SELECT name, user, role
        FROM `tabAOS Conversation Participant`
        WHERE conversation=%(conversation)s AND status='active' AND user != %(user)s
        ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, joined_at ASC, name ASC
        LIMIT 1
        FOR UPDATE
        """,
        {"conversation": conversation_id, "user": departing_user},
        as_dict=True,
    )
    if not rows:
        return None
    winner = rows[0]
    frappe.db.set_value("AOS Conversation Participant", winner.name, "role", ROLE_OWNER, update_modified=True)
    return str(winner.user)


def deactivate_account_memberships(user: str, *, limit: int = 500) -> int:
    """Deactivate one bounded batch while preserving group ownership invariants."""
    size = max(1, min(int(limit or 500), 1000))
    conversation_ids = frappe.db.sql(
        """SELECT conversation FROM `tabAOS Conversation Participant`
           WHERE user=%s AND status='active' ORDER BY conversation ASC LIMIT %s""",
        (user, size),
        pluck=True,
    )
    deactivated = 0
    for conversation_id in sorted({str(value) for value in conversation_ids if value}):
        conversation = get_conversation(conversation_id, for_update=True)
        if not conversation:
            continue
        lock_memberships(conversation_id)
        membership = get_membership(conversation_id, user, for_update=True, include_inactive=False)
        if not membership:
            continue
        if conversation.conversation_type == GROUP:
            transfer_owner_if_needed(conversation_id, user)
        mark_membership_inactive(membership_name=membership.name, status=LEFT)
        refresh_participant_count(conversation_id)
        increment_membership_version(conversation_id)
        deactivated += 1
    return deactivated
