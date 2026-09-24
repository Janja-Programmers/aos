"""Conversation and group-membership use cases for AOS Chat."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.api.shared.account_status import ensure_account_active
from aos.api.shared.auth import require_login
from aos.api.shared.blocking import ensure_not_blocked
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.api.shared.user_display import get_user_display_map
from aos.services.accounts.identity import public_account_id_for_user, resolve_account_reference
from aos.services.chat.cursors import decode_cursor, encode_cursor
from aos.services.chat.errors import ChatError
from aos.services.chat.events import publish_after_commit
from aos.services.chat.lock import authorize_locked_conversation, credential_state, token_valid
from aos.services.chat.membership import (
    ACTIVE,
    GROUP,
    GROUP_MANAGERS,
    LEFT,
    MAX_GROUP_PARTICIPANTS,
    MIN_GROUP_PARTICIPANTS,
    REMOVED,
    ROLE_ADMIN,
    ROLE_MEMBER,
    ROLE_OWNER,
    active_members,
    assert_group_manager,
    create_membership,
    direct_key,
    get_conversation,
    get_membership,
    increment_membership_version,
    lock_memberships,
    mark_membership_inactive,
    refresh_participant_count,
    require_active_membership,
    transfer_owner_if_needed,
)
from aos.services.media.media_service import MediaError, MediaService

from aos.services.chat.constants import (
    DELETE_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
    LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER,
    OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER,
)

CREATE_GROUP_LIMIT_PER_MINUTE_PER_USER = 20
UPDATE_GROUP_LIMIT_PER_MINUTE_PER_USER = 60
GROUP_MEMBERSHIP_LIMIT_PER_MINUTE_PER_USER = 60
LIST_GROUP_MEMBERS_LIMIT_PER_MINUTE_PER_USER = 120


def _rl(operation: str, user: str, limit: int):
    return rate_limit(
        key=rate_limit_key("chat", operation, user),
        ttl_seconds=60,
        limit=limit,
        message="Too many Chat requests. Please try again shortly.",
    )


def _resolve_active_account(account_id: str) -> str:
    user = resolve_account_reference(account_id)
    if not user or not frappe.db.exists("User", {"name": user, "enabled": 1}) or ensure_account_active(user):
        raise ChatError("Account not found.", code="CHAT_NOT_FOUND", http_status=404)
    return user


def _group_avatar_url_map(conversations: list[Any], *, user: str) -> dict[str, str]:
    media_ids = [str(row.avatar_media) for row in conversations if row.avatar_media]
    if not media_ids:
        return {}
    try:
        return MediaService().get_chat_group_avatar_url_map(media_ids, user=user)
    except Exception:
        return {}


def _last_message_preview(row: Any) -> str | None:
    if not getattr(row, "last_visible_message", None):
        return None
    if int(getattr(row, "last_visible_deleted", 0) or 0):
        return "This message was deleted"
    content = str(getattr(row, "last_visible_content", "") or "").strip()
    if content:
        return content
    if getattr(row, "last_visible_short", None):
        return "[Short]"
    if getattr(row, "last_visible_live", None):
        return "[Live]"
    if getattr(row, "last_visible_ad", None):
        return "[Ad]"
    if int(getattr(row, "last_visible_has_attachments", 0) or 0):
        return "[Attachment]"
    if getattr(row, "last_visible_call_id", None):
        return "[Call]"
    return "[Message]"


def _publish_group_update(*, conversation_id: str, recipient: str, payload: dict[str, Any]) -> None:
    """Publish group changes without leaking locked-chat membership metadata."""
    membership = get_membership(conversation_id, recipient, include_inactive=True)
    message = dict(payload)
    if membership and int(membership.is_locked or 0):
        message = {"conversation_id": conversation_id, "change": "refresh"}
    publish_after_commit(event="aos_group_updated", message=message, user=recipient)


def _serialize_conversation_rows(rows: list[Any], *, current_user: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    conversation_ids = [str(row.name) for row in rows]
    participant_rows = frappe.get_all(
        "AOS Conversation Participant",
        filters={"conversation": ["in", conversation_ids], "status": ACTIVE},
        fields=["conversation", "user", "role", "joined_at"],
        order_by="conversation asc, joined_at asc, name asc",
        limit=max(1, len(conversation_ids) * MAX_GROUP_PARTICIPANTS),
    )
    by_conversation: dict[str, list[Any]] = {}
    users: set[str] = set()
    for item in participant_rows:
        by_conversation.setdefault(str(item.conversation), []).append(item)
        if item.user:
            users.add(str(item.user))
    displays = get_user_display_map(users)
    avatar_urls = _group_avatar_url_map(rows, user=current_user)
    result: list[dict[str, Any]] = []
    for row in rows:
        members = by_conversation.get(str(row.name), [])
        my_member = next((m for m in members if m.user == current_user), None)
        payload: dict[str, Any] = {
            "id": row.name,
            "type": row.conversation_type,
            "title": row.title if row.conversation_type == GROUP else None,
            "avatar_media_id": row.avatar_media if row.conversation_type == GROUP else None,
            "avatar_url": avatar_urls.get(str(row.avatar_media)) if row.avatar_media else None,
            "participant_count": int(row.participant_count or len(members)),
            "my_role": my_member.role if my_member else getattr(row, "role", None),
            "unread_count": int(getattr(row, "unread_count", 0) or 0),
            "is_locked": bool(int(getattr(row, "is_locked", 0) or 0)),
            "last_message": _last_message_preview(row),
            "last_message_id": getattr(row, "last_visible_message", None),
            "last_message_at": getattr(row, "last_visible_message_at", None),
            "last_sender": public_account_id_for_user(getattr(row, "last_visible_sender", None)),
        }
        if row.conversation_type == "direct":
            peer = next((m for m in members if m.user != current_user), None)
            display = displays.get(str(peer.user)) if peer else None
            payload.update(
                {
                    "peer": {
                        "account_id": display.get("account_id") if display else None,
                        "display_name": display.get("display_name") if display else None,
                        "avatar": display.get("avatar") if display else None,
                        "is_deleted": bool(display.get("is_deleted")) if display else False,
                        "is_live": bool(display.get("is_live")) if display else False,
                        "live_id": display.get("live_id") if display else None,
                        "live_status": display.get("live_status") if display else None,
                    }
                    if peer
                    else None
                }
            )
        else:
            payload["peer"] = None
        result.append(payload)
    return result


def _conversation_list_query(*, user: str, locked: bool, limit: int, cursor: str | None) -> tuple[list[Any], str | None]:
    decoded = decode_cursor(cursor, kind="conversation", required_keys=("at", "id"))
    params: dict[str, Any] = {"user": user, "locked": 1 if locked else 0, "limit": limit + 1}
    cursor_sql = ""
    if decoded:
        params["cursor_at"] = decoded["at"]
        params["cursor_id"] = decoded["id"]
        cursor_sql = """
          AND (
                COALESCE(cp.last_visible_message_at, cp.joined_at) < %(cursor_at)s
             OR (COALESCE(cp.last_visible_message_at, cp.joined_at) = %(cursor_at)s AND c.name < %(cursor_id)s)
          )
        """
    rows = frappe.db.sql(
        f"""
        SELECT c.name, c.conversation_type, c.title, c.avatar_media, c.created_by,
               c.participant_count, c.membership_version, c.last_message, c.last_message_at, c.last_sender,
               cp.role, cp.unread_count, cp.is_locked, cp.last_visible_message,
               cp.last_visible_message_at, cp.last_visible_sender,
               m.content AS last_visible_content, m.message_type AS last_visible_type,
               m.ad AS last_visible_ad, m.short AS last_visible_short, m.live AS last_visible_live,
               m.call_id AS last_visible_call_id, m.has_attachments AS last_visible_has_attachments,
               m.deleted_for_everyone AS last_visible_deleted
        FROM `tabAOS Conversation Participant` cp
        INNER JOIN `tabAOS Conversation` c ON c.name=cp.conversation
        LEFT JOIN `tabAOS Message` m ON m.name=cp.last_visible_message
        WHERE cp.user=%(user)s AND cp.status='active' AND IFNULL(cp.is_hidden,0)=0
          AND IFNULL(cp.is_locked,0)=%(locked)s
          {cursor_sql}
        ORDER BY COALESCE(cp.last_visible_message_at, cp.joined_at) DESC, c.name DESC
        LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )
    more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if more and page:
        last = page[-1]
        at = last.last_visible_message_at or frappe.db.get_value(
            "AOS Conversation Participant", {"conversation": last.name, "user": user}, "joined_at"
        )
        next_cursor = encode_cursor("conversation", {"at": str(at), "id": str(last.name)})
    return page, next_cursor


def open_conversation_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("open_conversation", current_user, OPEN_CONVERSATION_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    other_user = _resolve_active_account(str(kwargs.get("user") or ""))
    if other_user == current_user:
        return fail("Cannot start a conversation with yourself.", error="CHAT_INVALID_REQUEST", http_status=422)
    if blocked := ensure_not_blocked(current_user=current_user, target_user=other_user, action="message"):
        return blocked
    key = direct_key(current_user, other_user)
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Conversation` WHERE conversation_type='direct' AND direct_key=%s LIMIT 1 FOR UPDATE",
        (key,),
        pluck=True,
    )
    if rows:
        conv_id = rows[0]
        lock_memberships(conv_id, [current_user, other_user])
        # Direct participants are durable; deleting a conversation only hides it.
        frappe.db.sql(
            """UPDATE `tabAOS Conversation Participant`
               SET status='active', left_at=NULL, is_hidden=0
               WHERE conversation=%s AND user IN %s""",
            (conv_id, (current_user, other_user)),
        )
    else:
        doc = frappe.new_doc("AOS Conversation")
        doc.conversation_type = "direct"
        doc.direct_key = key
        doc.created_by = current_user
        doc.participant_count = 2
        try:
            doc.insert(ignore_permissions=True)
            conv_id = doc.name
            create_membership(conversation_id=conv_id, user=current_user, role=ROLE_MEMBER, added_by=current_user, visible_from=doc.creation)
            create_membership(conversation_id=conv_id, user=other_user, role=ROLE_MEMBER, added_by=current_user, visible_from=doc.creation)
        except frappe.DuplicateEntryError:
            conv_id = frappe.db.get_value("AOS Conversation", {"direct_key": key, "conversation_type": "direct"}, "name")
            if not conv_id:
                raise
    row = frappe.db.sql(
        """
        SELECT c.name, c.conversation_type, c.title, c.avatar_media, c.created_by, c.participant_count,
               c.membership_version, c.last_message, c.last_message_at, c.last_sender,
               cp.role, cp.unread_count, cp.is_locked, cp.last_visible_message, cp.last_visible_message_at,
               cp.last_visible_sender, m.content AS last_visible_content, m.message_type AS last_visible_type,
               m.ad AS last_visible_ad, m.short AS last_visible_short, m.live AS last_visible_live,
               m.call_id AS last_visible_call_id, m.has_attachments AS last_visible_has_attachments,
               m.deleted_for_everyone AS last_visible_deleted
        FROM `tabAOS Conversation` c
        INNER JOIN `tabAOS Conversation Participant` cp ON cp.conversation=c.name AND cp.user=%s
        LEFT JOIN `tabAOS Message` m ON m.name=cp.last_visible_message
        WHERE c.name=%s LIMIT 1
        """,
        (current_user, conv_id),
        as_dict=True,
    )[0]
    return ok("Conversation ready.", data=_serialize_conversation_rows([row], current_user=current_user)[0])


def list_conversations_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("list_conversations", current_user, LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    limit = int(kwargs.get("limit") or 50)
    rows, next_cursor = _conversation_list_query(user=current_user, locked=False, limit=limit, cursor=kwargs.get("cursor"))
    return ok("Conversations loaded.", data={"items": _serialize_conversation_rows(rows, current_user=current_user), "next_cursor": next_cursor})


def list_locked_conversations_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("list_locked_conversations", current_user, LIST_CONVERSATIONS_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    state = credential_state(current_user)
    if state["hide_locked_chats"] and not token_valid(user=current_user, token=kwargs.get("lock_token")):
        # Hidden means the folder itself is undiscoverable without the secret-derived token.
        return ok("Locked conversations loaded.", data={"items": [], "next_cursor": None, "hidden": True})
    limit = int(kwargs.get("limit") or 50)
    rows, next_cursor = _conversation_list_query(user=current_user, locked=True, limit=limit, cursor=kwargs.get("cursor"))
    return ok(
        "Locked conversations loaded.",
        data={"items": _serialize_conversation_rows(rows, current_user=current_user), "next_cursor": next_cursor, "hidden": False},
    )


def delete_conversation_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("delete_conversation", current_user, DELETE_CONVERSATION_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    _conv, member = require_active_membership(conv_id, current_user, for_update=True)
    cleared_at = now_datetime()
    frappe.db.set_value(
        "AOS Conversation Participant",
        member.name,
        {
            "is_hidden": 1,
            "unread_count": 0,
            "cleared_before": cleared_at,
            "last_visible_message": None,
            "last_visible_message_at": None,
            "last_visible_sender": None,
        },
        update_modified=True,
    )
    return ok("Conversation hidden.", data={"conversation_id": conv_id, "cleared_before": cleared_at})


def _resolve_group_targets(values: list[str], *, actor: str) -> list[str]:
    users: list[str] = []
    for account_id in values:
        user = _resolve_active_account(account_id)
        if user == actor:
            continue
        if blocked := ensure_not_blocked(current_user=actor, target_user=user, action="add to group"):
            raise ChatError("One or more accounts cannot be added to this group.", code="CHAT_ACCESS_DENIED", http_status=403)
        users.append(user)
    return list(dict.fromkeys(users))


def create_group_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("create_group", current_user, CREATE_GROUP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    title = str(kwargs.get("title") or "").strip()
    if not title:
        return fail("Group title is required.", error="CHAT_INVALID_REQUEST", http_status=422)
    targets = _resolve_group_targets(kwargs.get("participant_ids") or [], actor=current_user)
    if len(targets) + 1 < MIN_GROUP_PARTICIPANTS:
        return fail("A group requires at least two participants.", error="CHAT_INVALID_REQUEST", http_status=422)
    if len(targets) + 1 > MAX_GROUP_PARTICIPANTS:
        return fail("Group participant limit exceeded.", error="CHAT_INPUT_TOO_LARGE", http_status=413)
    avatar_media_id = str(kwargs.get("avatar_media_id") or "").strip() or None
    if avatar_media_id:
        MediaService().assert_media_ready_for_attach(media_id=avatar_media_id, user=current_user, purpose="chat_group_avatar")
    doc = frappe.new_doc("AOS Conversation")
    doc.conversation_type = GROUP
    doc.title = title
    doc.created_by = current_user
    doc.participant_count = len(targets) + 1
    doc.membership_version = 1
    doc.insert(ignore_permissions=True)
    create_membership(conversation_id=doc.name, user=current_user, role=ROLE_OWNER, added_by=current_user, visible_from=doc.creation)
    for user in targets:
        create_membership(conversation_id=doc.name, user=user, role=ROLE_MEMBER, added_by=current_user, visible_from=doc.creation)
    if avatar_media_id:
        MediaService().attach_media(
            media_id=avatar_media_id,
            user=current_user,
            purpose="chat_group_avatar",
            attached_doctype="AOS Conversation",
            attached_name=doc.name,
            attached_field="avatar_media",
        )
        frappe.db.set_value("AOS Conversation", doc.name, "avatar_media", avatar_media_id, update_modified=False)
    _publish_group_update(conversation_id=doc.name, recipient=current_user, payload={"conversation_id": doc.name, "change": "created"})
    row, _member = require_active_membership(doc.name, current_user)
    qrow = frappe.db.sql(
        """
        SELECT c.name, c.conversation_type, c.title, c.avatar_media, c.created_by, c.participant_count,
               c.membership_version, c.last_message, c.last_message_at, c.last_sender,
               cp.role, cp.unread_count, cp.is_locked, cp.last_visible_message, cp.last_visible_message_at,
               cp.last_visible_sender, NULL AS last_visible_content, NULL AS last_visible_type,
               NULL AS last_visible_ad, NULL AS last_visible_short, NULL AS last_visible_live,
               NULL AS last_visible_call_id, 0 AS last_visible_has_attachments, 0 AS last_visible_deleted
        FROM `tabAOS Conversation` c JOIN `tabAOS Conversation Participant` cp
          ON cp.conversation=c.name AND cp.user=%s WHERE c.name=%s
        """,
        (current_user, row.name), as_dict=True,
    )[0]
    return ok("Group created.", data=_serialize_conversation_rows([qrow], current_user=current_user)[0])


def update_group_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("update_group", current_user, UPDATE_GROUP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, member = require_active_membership(conv_id, current_user, for_update=True)
    assert_group_manager(conv, member)
    updates: dict[str, Any] = {}
    if "title" in kwargs:
        title = str(kwargs.get("title") or "").strip()
        if not title:
            return fail("Group title is required.", error="CHAT_INVALID_REQUEST", http_status=422)
        updates["title"] = title
    media = MediaService()
    old_avatar = str(conv.avatar_media or "").strip() or None
    new_avatar = str(kwargs.get("avatar_media_id") or "").strip() or None
    remove_avatar = bool(int(kwargs.get("remove_avatar") or 0))
    if new_avatar and remove_avatar:
        return fail("Choose an avatar or remove it, not both.", error="CHAT_INVALID_REQUEST", http_status=422)
    if new_avatar and new_avatar != old_avatar:
        media.assert_media_ready_for_attach(media_id=new_avatar, user=current_user, purpose="chat_group_avatar")
        media.attach_media(
            media_id=new_avatar, user=current_user, purpose="chat_group_avatar",
            attached_doctype="AOS Conversation", attached_name=conv_id, attached_field="avatar_media",
            replacing_media_id=old_avatar,
        )
        if old_avatar:
            media.release_media(
                media_id=old_avatar, user=current_user, attached_doctype="AOS Conversation",
                attached_name=conv_id, replacement_media_id=new_avatar,
            )
        updates["avatar_media"] = new_avatar
    elif remove_avatar and old_avatar:
        media.release_media(media_id=old_avatar, user=current_user, attached_doctype="AOS Conversation", attached_name=conv_id)
        updates["avatar_media"] = None
    if updates:
        frappe.db.set_value("AOS Conversation", conv_id, updates, update_modified=True)
        increment_membership_version(conv_id)
    for target in active_members(conv_id):
        _publish_group_update(conversation_id=conv_id, recipient=str(target.user), payload={"conversation_id": conv_id, "change": "metadata"})
    return ok("Group updated.", data={"conversation_id": conv_id})


def add_group_members_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("add_group_members", current_user, GROUP_MEMBERSHIP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, actor = require_active_membership(conv_id, current_user, for_update=True)
    assert_group_manager(conv, actor)
    lock_memberships(conv_id)
    targets = _resolve_group_targets(kwargs.get("participant_ids") or [], actor=current_user)
    if not targets:
        return fail("participant_ids is required.", error="CHAT_INVALID_REQUEST", http_status=422)
    current_count = int(frappe.db.count("AOS Conversation Participant", {"conversation": conv_id, "status": ACTIVE}) or 0)
    to_add = [u for u in targets if not get_membership(conv_id, u, include_inactive=False)]
    if current_count + len(to_add) > MAX_GROUP_PARTICIPANTS:
        return fail("Group participant limit exceeded.", error="CHAT_INPUT_TOO_LARGE", http_status=413)
    for user in to_add:
        create_membership(conversation_id=conv_id, user=user, role=ROLE_MEMBER, added_by=current_user, visible_from=now_datetime())
    refresh_participant_count(conv_id)
    if to_add:
        increment_membership_version(conv_id)
        recipients = [str(m.user) for m in active_members(conv_id)]
        for recipient in recipients:
            _publish_group_update(conversation_id=conv_id, recipient=recipient, payload={"conversation_id": conv_id, "change": "members_added", "account_ids": [public_account_id_for_user(u) for u in to_add]})
    return ok("Group members updated.", data={"conversation_id": conv_id, "added": [public_account_id_for_user(u) for u in to_add]})


def remove_group_member_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("remove_group_member", current_user, GROUP_MEMBERSHIP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, actor = require_active_membership(conv_id, current_user, for_update=True)
    assert_group_manager(conv, actor)
    target_user = _resolve_active_account(str(kwargs.get("account_id") or ""))
    lock_memberships(conv_id, [current_user, target_user])
    target = get_membership(conv_id, target_user, for_update=True, include_inactive=False)
    if not target:
        return fail("Group member not found.", error="CHAT_NOT_FOUND", http_status=404)
    if target_user == current_user:
        return fail("Use leave_group to leave a group.", error="CHAT_INVALID_REQUEST", http_status=422)
    if actor.role == ROLE_ADMIN and target.role in {ROLE_OWNER, ROLE_ADMIN}:
        return fail("Admins cannot remove the owner or another admin.", error="CHAT_ACCESS_DENIED", http_status=403)
    if target.role == ROLE_OWNER:
        return fail("Transfer ownership before removing the owner.", error="CHAT_INVALID_STATE", http_status=409)
    mark_membership_inactive(membership_name=target.name, status=REMOVED)
    refresh_participant_count(conv_id)
    increment_membership_version(conv_id)
    recipients = [str(m.user) for m in active_members(conv_id)] + [target_user]
    for recipient in dict.fromkeys(recipients):
        _publish_group_update(conversation_id=conv_id, recipient=recipient, payload={"conversation_id": conv_id, "change": "member_removed", "account_id": public_account_id_for_user(target_user)})
    return ok("Group member removed.", data={"conversation_id": conv_id, "account_id": public_account_id_for_user(target_user)})


def set_group_member_role_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("set_group_member_role", current_user, GROUP_MEMBERSHIP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, actor = require_active_membership(conv_id, current_user, for_update=True)
    if conv.conversation_type != GROUP or actor.role != ROLE_OWNER:
        return fail("Only the group owner can change admin roles.", error="CHAT_ACCESS_DENIED", http_status=403)
    target_user = _resolve_active_account(str(kwargs.get("account_id") or ""))
    role = str(kwargs.get("role") or "").strip().lower()
    if role not in {ROLE_ADMIN, ROLE_MEMBER}:
        return fail("Role must be admin or member.", error="CHAT_INVALID_REQUEST", http_status=422)
    lock_memberships(conv_id, [target_user])
    target = get_membership(conv_id, target_user, for_update=True, include_inactive=False)
    if not target or target.role == ROLE_OWNER:
        return fail("Group member not found.", error="CHAT_NOT_FOUND", http_status=404)
    frappe.db.set_value("AOS Conversation Participant", target.name, "role", role, update_modified=True)
    increment_membership_version(conv_id)
    for recipient in [str(m.user) for m in active_members(conv_id)]:
        _publish_group_update(conversation_id=conv_id, recipient=recipient, payload={"conversation_id": conv_id, "change": "role", "account_id": public_account_id_for_user(target_user), "role": role})
    return ok("Group role updated.", data={"conversation_id": conv_id, "account_id": public_account_id_for_user(target_user), "role": role})


def transfer_group_ownership_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("transfer_group_ownership", current_user, GROUP_MEMBERSHIP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, actor = require_active_membership(conv_id, current_user, for_update=True)
    if conv.conversation_type != GROUP or actor.role != ROLE_OWNER:
        return fail("Only the group owner can transfer ownership.", error="CHAT_ACCESS_DENIED", http_status=403)
    target_user = _resolve_active_account(str(kwargs.get("account_id") or ""))
    if target_user == current_user:
        return ok("Ownership unchanged.", data={"conversation_id": conv_id, "owner": public_account_id_for_user(current_user)})
    lock_memberships(conv_id, [current_user, target_user])
    target = get_membership(conv_id, target_user, for_update=True, include_inactive=False)
    if not target:
        return fail("Group member not found.", error="CHAT_NOT_FOUND", http_status=404)
    frappe.db.set_value("AOS Conversation Participant", actor.name, "role", ROLE_ADMIN, update_modified=True)
    frappe.db.set_value("AOS Conversation Participant", target.name, "role", ROLE_OWNER, update_modified=True)
    increment_membership_version(conv_id)
    for recipient in [str(m.user) for m in active_members(conv_id)]:
        _publish_group_update(conversation_id=conv_id, recipient=recipient, payload={"conversation_id": conv_id, "change": "owner", "account_id": public_account_id_for_user(target_user)})
    return ok("Group ownership transferred.", data={"conversation_id": conv_id, "owner": public_account_id_for_user(target_user)})


def leave_group_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("leave_group", current_user, GROUP_MEMBERSHIP_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, member = require_active_membership(conv_id, current_user, for_update=True)
    if conv.conversation_type != GROUP:
        return fail("Only group conversations can be left.", error="CHAT_INVALID_REQUEST", http_status=422)
    lock_memberships(conv_id)
    new_owner = transfer_owner_if_needed(conv_id, current_user)
    mark_membership_inactive(membership_name=member.name, status=LEFT)
    refresh_participant_count(conv_id)
    increment_membership_version(conv_id)
    recipients = [str(m.user) for m in active_members(conv_id)] + [current_user]
    for recipient in dict.fromkeys(recipients):
        _publish_group_update(conversation_id=conv_id, recipient=recipient, payload={"conversation_id": conv_id, "change": "member_left", "account_id": public_account_id_for_user(current_user), "new_owner": public_account_id_for_user(new_owner) if new_owner else None})
    return ok("You left the group.", data={"conversation_id": conv_id})


def list_group_members_impl(**kwargs):
    current_user, err = require_login()
    if err:
        return err
    if (limited := _rl("list_group_members", current_user, LIST_GROUP_MEMBERS_LIMIT_PER_MINUTE_PER_USER)):
        return limited
    conv_id = kwargs.get("conversation_id")
    authorize_locked_conversation(user=current_user, conversation_id=conv_id, lock_token=kwargs.get("lock_token"))
    conv, _member = require_active_membership(conv_id, current_user)
    if conv.conversation_type != GROUP:
        return fail("Conversation is not a group.", error="CHAT_INVALID_REQUEST", http_status=422)
    limit = int(kwargs.get("limit") or 100)
    decoded = decode_cursor(kwargs.get("cursor"), kind="group_member", required_keys=("joined_at", "id"))
    params: dict[str, Any] = {"conversation": conv_id, "limit": limit + 1}
    cursor_sql = ""
    if decoded:
        params.update({"joined_at": decoded["joined_at"], "id": decoded["id"]})
        cursor_sql = "AND (joined_at > %(joined_at)s OR (joined_at=%(joined_at)s AND name > %(id)s))"
    rows = frappe.db.sql(
        f"""
        SELECT name, user, role, status, joined_at
        FROM `tabAOS Conversation Participant`
        WHERE conversation=%(conversation)s AND status='active' {cursor_sql}
        ORDER BY joined_at ASC, name ASC LIMIT %(limit)s
        """,
        params,
        as_dict=True,
    )
    more = len(rows) > limit
    page = rows[:limit]
    displays = get_user_display_map([str(row.user) for row in page if row.user])
    items = []
    for row in page:
        display = displays.get(str(row.user)) or {}
        items.append({
            "account_id": display.get("account_id"),
            "display_name": display.get("display_name"),
            "avatar": display.get("avatar"),
            "is_deleted": bool(display.get("is_deleted")),
            "role": row.role,
            "joined_at": row.joined_at,
        })
    next_cursor = encode_cursor("group_member", {"joined_at": str(page[-1].joined_at), "id": str(page[-1].name)}) if more and page else None
    return ok("Group members loaded.", data={"items": items, "next_cursor": next_cursor})
