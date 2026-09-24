"""Durable per-recipient delivery/read state for direct and group Chat."""
from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from aos.services.chat.constants import MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER, MARK_READ_LIMIT_PER_MINUTE_PER_USER
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.services.chat.events import publish_after_commit
from aos.services.chat.lock import authorize_locked_conversation
from aos.services.chat.membership import require_active_membership
from aos.services.chat.identifiers import generate_message_state_id
from aos.services.chat.repository import lock_conversations

BATCH_SIZE = 500
MAX_BATCHES_PER_REQUEST = 4


def _limit(operation: str, user: str, maximum: int):
    return rate_limit(key=rate_limit_key("chat", operation, user), ttl_seconds=60, limit=maximum,
                      message="Too many Chat status requests. Please try again shortly.")


def _upsert_states(*, conversation_id: str, user: str, field: str, timestamp, membership) -> tuple[int, set[str]]:
    rows = frappe.db.sql(
        """
        SELECT m.name, m.sender
        FROM `tabAOS Message` m
        LEFT JOIN `tabAOS Message User State` s ON s.message=m.name AND s.user=%(user)s
        WHERE m.conversation=%(conversation)s AND m.sender != %(user)s
          AND m.creation >= %(visible_from)s
          AND (%(cleared_before)s IS NULL OR m.creation > %(cleared_before)s)
          AND s.hidden_at IS NULL
          AND s.{field} IS NULL
        ORDER BY m.creation ASC, m.name ASC
        LIMIT %(limit)s
        """.format(field=field),
        {"conversation": conversation_id, "user": user, "visible_from": membership.visible_from,
         "cleared_before": membership.cleared_before, "limit": BATCH_SIZE}, as_dict=True,
    )
    if not rows:
        return 0, set()
    for row in rows:
        existing = frappe.db.get_value("AOS Message User State", {"message": row.name, "user": user}, "name")
        values = {field: timestamp}
        if field == "read_at":
            values["delivered_at"] = timestamp
        if existing:
            frappe.db.set_value("AOS Message User State", existing, values, update_modified=False)
        else:
            doc = frappe.new_doc("AOS Message User State")
            doc.name = generate_message_state_id()
            doc.message = row.name; doc.conversation = conversation_id; doc.user = user
            doc.delivered_at = timestamp
            if field == "read_at": doc.read_at = timestamp
            doc.insert(ignore_permissions=True)
    return len(rows), {str(row.sender) for row in rows if row.sender and row.sender != "Administrator"}


def _mark(*, operation: str, field: str, limit: int, kwargs):
    user, err = require_login()
    if err: return err
    if limited := _limit(operation, user, limit): return limited
    conversation_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=user, conversation_id=conversation_id, lock_token=kwargs.get("lock_token"))
    lock_conversations([conversation_id])
    _conversation, membership = require_active_membership(conversation_id, user, for_update=True)
    now = now_datetime()
    updated_count = 0
    senders: set[str] = set()
    for _ in range(MAX_BATCHES_PER_REQUEST):
        count, batch_senders = _upsert_states(
            conversation_id=conversation_id, user=user, field=field, timestamp=now, membership=membership
        )
        updated_count += count
        senders.update(batch_senders)
        if count < BATCH_SIZE:
            break
    has_more = bool(
        frappe.db.sql(
            f"""SELECT 1 FROM `tabAOS Message` m
                LEFT JOIN `tabAOS Message User State` s ON s.message=m.name AND s.user=%(user)s
                WHERE m.conversation=%(conversation)s AND m.sender != %(user)s
                  AND m.creation >= %(visible_from)s
                  AND (%(cleared_before)s IS NULL OR m.creation > %(cleared_before)s)
                  AND s.hidden_at IS NULL AND s.{field} IS NULL LIMIT 1""",
            {"conversation": conversation_id, "user": user, "visible_from": membership.visible_from, "cleared_before": membership.cleared_before},
        )
    )
    if field == "read_at":
        unread = max(0, int(membership.unread_count or 0))
        remaining = max(0, unread - updated_count) if has_more else 0
        frappe.db.set_value("AOS Conversation Participant", membership.name, "unread_count", remaining, update_modified=False)
    payload = {"conversation_id": conversation_id, "reader_account_id": None, "state": "read" if field=="read_at" else "delivered", "at": now}
    from aos.services.accounts.identity import public_account_id_for_user
    payload["reader_account_id"] = public_account_id_for_user(user)
    for sender in senders:
        publish_after_commit(event="aos_message_status", message=payload, user=sender)
    return ok("Message state updated.", data={"conversation_id": conversation_id, "state": payload["state"], "updated_at": now, "updated_count": updated_count, "has_more": has_more})


def mark_delivered_impl(**kwargs):
    return _mark(operation="mark_delivered", field="delivered_at", limit=MARK_DELIVERED_LIMIT_PER_MINUTE_PER_USER, kwargs=kwargs)


def mark_read_impl(**kwargs):
    return _mark(operation="mark_read", field="read_at", limit=MARK_READ_LIMIT_PER_MINUTE_PER_USER, kwargs=kwargs)
