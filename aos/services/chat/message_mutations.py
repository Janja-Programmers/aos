"""Mutations for messages, private visibility, stars and reactions."""
from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import now_datetime

from aos.services.chat.constants import (
    CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER, DELETE_MESSAGES_LIMIT_PER_MINUTE_PER_USER,
    EDIT_MESSAGE_LIMIT_PER_MINUTE_PER_USER, FORWARD_MESSAGE_LIMIT_PER_MINUTE_PER_USER,
    LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER, SET_MESSAGE_REACTION_LIMIT_PER_MINUTE_PER_USER,
    SET_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER,
)
from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import fail, ok
from aos.services.chat.cursors import decode_cursor, encode_cursor
from aos.services.chat.events import publish_after_commit
from aos.services.chat.lock import authorize_locked_conversation, token_valid
from aos.services.chat.membership import ACTIVE, active_member_users, active_members, require_active_membership
from aos.services.chat.message_ops import (
    _idempotency_digest, _message_fields, _participant_users, _reaction_maps,
    _receipt_map, _serialize_one, _starred, _update_conversation_after_send,
)
from aos.services.chat.projections import serialize_messages
from aos.services.chat.repository import lock_conversations, lock_messages


def _rl(name: str, user: str, limit: int):
    return rate_limit(key=rate_limit_key("chat", name, user), ttl_seconds=60, limit=limit,
                      message="Too many Chat requests. Please try again shortly.")


def _message_for_user(message_id: str, user: str, *, lock_token: str | None = None, for_update: bool = False):
    row = frappe.db.get_value("AOS Message", message_id, _message_fields(), as_dict=True)
    if not row:
        return None, None, None
    authorize_locked_conversation(user=user, conversation_id=row.conversation, lock_token=lock_token)
    conversation, membership = require_active_membership(row.conversation, user, for_update=for_update)
    hidden = frappe.db.get_value("AOS Message User State", {"message": message_id, "user": user}, "hidden_at")
    if hidden or row.creation < membership.visible_from or (membership.cleared_before and row.creation <= membership.cleared_before):
        return None, None, None
    return row, conversation, membership


def _recompute_member_preview(conversation_id: str, user: str) -> None:
    membership = frappe.db.get_value(
        "AOS Conversation Participant", {"conversation": conversation_id, "user": user},
        ["name", "visible_from", "cleared_before"], as_dict=True,
    )
    if not membership:
        return
    rows = frappe.db.sql(
        """
        SELECT m.name, m.sender, m.creation, m.content, m.message_type, m.deleted_for_everyone,
               m.has_attachments, m.ad, m.short, m.live
        FROM `tabAOS Message` m
        LEFT JOIN `tabAOS Message User State` s ON s.message=m.name AND s.user=%(user)s
        WHERE m.conversation=%(conversation)s AND m.creation >= %(visible_from)s
          AND (%(cleared_before)s IS NULL OR m.creation > %(cleared_before)s)
          AND s.hidden_at IS NULL
        ORDER BY m.creation DESC, m.name DESC LIMIT 1
        """,
        {"user": user, "conversation": conversation_id, "visible_from": membership.visible_from,
         "cleared_before": membership.cleared_before}, as_dict=True,
    )
    if not rows:
        values = {"last_visible_message": None, "last_visible_message_at": None, "last_visible_sender": None}
    else:
        row = rows[0]
        if int(row.deleted_for_everyone or 0):
            preview = "This message was deleted"
        elif row.content:
            preview = str(row.content)
        elif row.short:
            preview = "[Short]"
        elif row.live:
            preview = "[Live]"
        elif row.ad:
            preview = "[Ad]"
        elif int(row.has_attachments or 0):
            preview = "[Attachment]"
        else:
            preview = "[Message]"
        values = {"last_visible_message": row.name, "last_visible_message_at": row.creation,
                  "last_visible_sender": row.sender, "last_visible_message_text": preview}
    # last_visible_message_text is a query alias, not a column.
    values.pop("last_visible_message_text", None)
    frappe.db.set_value("AOS Conversation Participant", membership.name, values, update_modified=False)


def _publish_members(conversation_id: str, event: str, payload: dict[str, Any], *, exclude: str | None = None) -> None:
    for user in active_member_users(conversation_id, exclude_user=exclude):
        publish_after_commit(event=event, message=payload, user=user)


def edit_message_impl(**kwargs):
    user, err = require_login()
    if err: return err
    if limited := _rl("edit_message", user, EDIT_MESSAGE_LIMIT_PER_MINUTE_PER_USER): return limited
    message_id, content = kwargs.get("message_id"), str(kwargs.get("content") or "").strip()
    if not content: return fail("content is required.", error="CHAT_INVALID_REQUEST", http_status=422)
    conv_id = frappe.db.get_value("AOS Message", message_id, "conversation")
    if not conv_id: return fail("Message not found.", error="CHAT_NOT_FOUND", http_status=404)
    lock_conversations([conv_id]); lock_messages([message_id])
    msg, conversation, _ = _message_for_user(message_id, user, lock_token=kwargs.get("lock_token"), for_update=True)
    if not msg: return fail("Message not found.", error="CHAT_NOT_FOUND", http_status=404)
    if msg.sender != user: return fail("You can only edit your own messages.", error="CHAT_ACCESS_DENIED", http_status=403)
    if int(msg.deleted_for_everyone or 0) or msg.message_type not in {"text", "mixed"}:
        return fail("This message cannot be edited.", error="CHAT_INVALID_STATE", http_status=409)
    if str(msg.content or "").strip() == content:
        return ok("Message unchanged.", data=_serialize_one(message_id, viewer=user, conversation_type=conversation.conversation_type, participant_users=_participant_users(conv_id)))
    now = now_datetime()
    frappe.db.set_value("AOS Message", message_id, {"original_content": msg.original_content or msg.content,
        "content": content, "is_edited": 1, "edited_at": now}, update_modified=True)
    # Update any inbox whose preview points at this message without scanning history.
    frappe.db.sql("""UPDATE `tabAOS Conversation` SET last_message=%s WHERE name=%s AND last_message_at=%s""", (content, conv_id, msg.creation))
    payload = {"conversation_id": conv_id, "message_id": message_id}
    _publish_members(conv_id, "aos_message_edited", payload, exclude=user)
    return ok("Message edited.", data=_serialize_one(message_id, viewer=user, conversation_type=conversation.conversation_type, participant_users=_participant_users(conv_id)))


def _upsert_hidden(message_id: str, conversation_id: str, user: str, hidden_at) -> None:
    existing = frappe.db.get_value("AOS Message User State", {"message": message_id, "user": user}, "name")
    if existing:
        frappe.db.set_value("AOS Message User State", existing, "hidden_at", hidden_at, update_modified=False)
    else:
        doc = frappe.new_doc("AOS Message User State"); doc.message=message_id; doc.conversation=conversation_id; doc.user=user; doc.hidden_at=hidden_at; doc.insert(ignore_permissions=True)


def delete_messages_impl(**kwargs):
    user, err = require_login()
    if err: return err
    if limited := _rl("delete_messages", user, DELETE_MESSAGES_LIMIT_PER_MINUTE_PER_USER): return limited
    ids = kwargs.get("message_ids") or []
    if not ids: return fail("message_ids is required.", error="CHAT_INVALID_REQUEST", http_status=422)
    convs = frappe.get_all("AOS Message", filters={"name":["in", ids]}, pluck="conversation", limit=len(ids))
    if len(convs) != len(ids): return fail("Message not found.", error="CHAT_NOT_FOUND", http_status=404)
    for conv in set(convs): authorize_locked_conversation(user=user, conversation_id=conv, lock_token=kwargs.get("lock_token")); require_active_membership(conv,user)
    lock_conversations(convs); lock_messages(ids)
    scope = kwargs.get("delete_scope") or "me"; now = now_datetime(); affected=set()
    for mid in ids:
        msg, _conv, member = _message_for_user(mid, user, lock_token=kwargs.get("lock_token"), for_update=True)
        if not msg: return fail("Message not found.", error="CHAT_NOT_FOUND", http_status=404)
        affected.add(msg.conversation)
        if scope == "everyone":
            if msg.sender != user or msg.message_type == "system":
                return fail("Message cannot be deleted for everyone.", error="CHAT_ACCESS_DENIED", http_status=403)
            frappe.db.set_value("AOS Message", mid, {"deleted_for_everyone":1,"deleted_for_everyone_at":now}, update_modified=True)
            _publish_members(msg.conversation, "aos_message_deleted", {"conversation_id":msg.conversation,"message_id":mid,"scope":"everyone"}, exclude=user)
        else:
            state = frappe.db.get_value("AOS Message User State", {"message": mid, "user": user}, ["read_at"], as_dict=True)
            _upsert_hidden(mid, msg.conversation, user, now)
            if msg.sender != user and not (state and state.read_at) and int(member.unread_count or 0) > 0:
                frappe.db.set_value(
                    "AOS Conversation Participant", member.name, "unread_count",
                    max(0, int(member.unread_count or 0) - 1), update_modified=False,
                )
                member.unread_count = max(0, int(member.unread_count or 0) - 1)
    for conv in affected: _recompute_member_preview(conv, user)
    return ok("Messages deleted.", data={"message_ids": ids, "delete_scope": scope})


def clear_chat_impl(**kwargs):
    user, err=require_login()
    if err:return err
    if limited:=_rl("clear_chat",user,CLEAR_CHAT_LIMIT_PER_MINUTE_PER_USER):return limited
    conv=kwargs.get("conversation_id"); authorize_locked_conversation(user=user,conversation_id=conv,lock_token=kwargs.get("lock_token"))
    lock_conversations([conv]); _c,m=require_active_membership(conv,user,for_update=True); now=now_datetime()
    frappe.db.set_value("AOS Conversation Participant",m.name,{"cleared_before":now,"unread_count":0,"last_visible_message":None,"last_visible_message_at":None,"last_visible_sender":None},update_modified=True)
    return ok("Chat cleared.",data={"conversation_id":conv,"cleared_before":now})


def set_message_star_impl(**kwargs):
    user,err=require_login()
    if err:return err
    if limited:=_rl("set_message_star",user,SET_MESSAGE_STAR_LIMIT_PER_MINUTE_PER_USER):return limited
    mid=kwargs.get("message_id"); conv_id=frappe.db.get_value("AOS Message",mid,"conversation")
    if not conv_id:return fail("Message not found.",error="CHAT_NOT_FOUND",http_status=404)
    lock_conversations([conv_id]); lock_messages([mid])
    msg,_,_= _message_for_user(mid,user,lock_token=kwargs.get("lock_token"),for_update=True)
    if not msg:return fail("Message not found.",error="CHAT_NOT_FOUND",http_status=404)
    desired=bool(int(kwargs.get("starred") or 0)); existing=frappe.db.get_value("AOS Message Star",{"message":mid,"user":user},"name")
    if desired and not existing:
        doc=frappe.new_doc("AOS Message Star");doc.message=mid;doc.conversation=msg.conversation;doc.user=user;doc.insert(ignore_permissions=True)
    elif not desired and existing: frappe.delete_doc("AOS Message Star",existing,ignore_permissions=True)
    return ok("Message star updated.",data={"message_id":mid,"starred":desired})


def list_starred_messages_impl(**kwargs):
    user,err=require_login()
    if err:return err
    if limited:=_rl("list_starred_messages",user,LIST_STARRED_MESSAGES_LIMIT_PER_MINUTE_PER_USER):return limited
    conv=kwargs.get("conversation_id")
    if conv: authorize_locked_conversation(user=user,conversation_id=conv,lock_token=kwargs.get("lock_token")); conversation,_=require_active_membership(conv,user)
    limit=int(kwargs.get("limit") or 50); cur=decode_cursor(kwargs.get("cursor"),kind="star",required_keys=("at","id")); params={"user":user,"limit":limit+1}; where=""
    if conv: params["conversation"]=conv; where+=" AND s.conversation=%(conversation)s"
    else:
        # Hidden locked chats are never discoverable through the normal private list.
        where+=" AND NOT (cp.is_locked=1 AND EXISTS (SELECT 1 FROM `tabAOS Chat Lock Credential` lc WHERE lc.user=%(user)s AND lc.hide_locked_chats=1))"
    if cur: params.update({"at":cur["at"],"id":cur["id"]});where+=" AND (s.creation<%(at)s OR (s.creation=%(at)s AND s.name<%(id)s))"
    rows=frappe.db.sql(f"""SELECT m.*, s.creation AS star_created, s.name AS star_id, c.conversation_type
      FROM `tabAOS Message Star` s JOIN `tabAOS Message` m ON m.name=s.message JOIN `tabAOS Conversation` c ON c.name=s.conversation
      JOIN `tabAOS Conversation Participant` cp ON cp.conversation=s.conversation AND cp.user=s.user AND cp.status='active'
      LEFT JOIN `tabAOS Message User State` us ON us.message=m.name AND us.user=s.user
      WHERE s.user=%(user)s AND us.hidden_at IS NULL AND m.creation>=cp.visible_from AND (cp.cleared_before IS NULL OR m.creation>cp.cleared_before){where}
      ORDER BY s.creation DESC,s.name DESC LIMIT %(limit)s""",params,as_dict=True)
    more=len(rows)>limit; page=rows[:limit]; out=[]
    # Serialize per conversation type in bounded groups.
    for row in page:
        out.append(_serialize_one(row.name,viewer=user,conversation_type=row.conversation_type,participant_users=_participant_users(row.conversation)))
    nxt=encode_cursor("star",{"at":str(page[-1].star_created),"id":str(page[-1].star_id)}) if more and page else None
    return ok("Starred messages fetched.",data={"items":out,"next_cursor":nxt,"has_more":more})


def set_message_reaction_impl(**kwargs):
    user,err=require_login()
    if err:return err
    if limited:=_rl("set_message_reaction",user,SET_MESSAGE_REACTION_LIMIT_PER_MINUTE_PER_USER):return limited
    mid=kwargs.get("message_id"); conv_id=frappe.db.get_value("AOS Message",mid,"conversation")
    if not conv_id:return fail("Message not found.",error="CHAT_NOT_FOUND",http_status=404)
    lock_conversations([conv_id]); lock_messages([mid])
    msg,_,_= _message_for_user(mid,user,lock_token=kwargs.get("lock_token"),for_update=True)
    if not msg or int(msg.deleted_for_everyone or 0):return fail("Message not found.",error="CHAT_NOT_FOUND",http_status=404)
    emoji=str(kwargs.get("emoji") or "").strip(); existing=frappe.db.get_value("AOS Message Reaction",{"message":mid,"user":user},"name")
    if not emoji and existing: frappe.delete_doc("AOS Message Reaction",existing,ignore_permissions=True)
    elif emoji and existing: frappe.db.set_value("AOS Message Reaction",existing,"emoji",emoji,update_modified=True)
    elif emoji:
        doc=frappe.new_doc("AOS Message Reaction");doc.message=mid;doc.conversation=msg.conversation;doc.user=user;doc.emoji=emoji;doc.insert(ignore_permissions=True)
    _publish_members(msg.conversation,"aos_message_reaction",{"conversation_id":msg.conversation,"message_id":mid},exclude=user)
    return ok("Message reaction updated.",data={"message_id":mid,"emoji":emoji or None})


def forward_message_impl(**kwargs):
    user,err=require_login()
    if err:return err
    if limited:=_rl("forward_message",user,FORWARD_MESSAGE_LIMIT_PER_MINUTE_PER_USER):return limited
    source_id=kwargs.get("message_id"); source,_,_= _message_for_user(source_id,user,lock_token=kwargs.get("lock_token"))
    if not source or int(source.deleted_for_everyone or 0) or source.message_type=="system": return fail("Message not found.",error="CHAT_NOT_FOUND",http_status=404)
    targets=kwargs.get("target_conversation_ids") or []
    if not targets:return fail("target_conversation_ids is required.",error="CHAT_INVALID_REQUEST",http_status=422)
    attachment_rows=frappe.get_all("AOS Message Attachment",filters={"message":source_id},fields=["media","sort_order"],order_by="sort_order asc,name asc",limit=10)
    lock_conversations(targets); results=[]
    from aos.services.chat.message_ops import send_message_for_user
    for idx,target in enumerate(targets):
        # Reuse immutable Media references through normal Media authorization; attachment input remains canonical media_id only.
        key=str(kwargs.get("idempotency_key") or "").strip(); derived=f"{key}:{source_id}:{idx}:{target}" if key else None
        result=send_message_for_user(current_user=user,conversation_id=target,content=source.content or "",
            attachments=[{"media_id":r.media} for r in attachment_rows],ad=source.ad,short=source.short,live=source.live,
            idempotency_key=derived,lock_token=kwargs.get("lock_token"))
        if result.get("ok") is not True:return result
        new_id=(result.get("data") or {}).get("id")
        if new_id: frappe.db.set_value("AOS Message",new_id,{"is_forwarded":1,"forwarded_from_message":source_id,"forwarded_from_conversation":source.conversation},update_modified=False)
        results.append(result.get("data"))
    return ok("Message forwarded.",data={"items":results})
