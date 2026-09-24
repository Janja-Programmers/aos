"""HA-safe Chat presence and ephemeral typing fanout."""
from __future__ import annotations

import frappe
from frappe.utils import now_datetime, time_diff_in_seconds

from aos.services.chat.constants import (
    GET_PRESENCE_LIMIT_PER_MINUTE_PER_USER, ONLINE_THRESHOLD_SECONDS,
    PRESENCE_ACTIVITY_WRITE_THROTTLE_SECONDS, PRESENCE_BROADCAST_THROTTLE_SECONDS,
    PRESENCE_MAX_PEERS, SEND_TYPING_LIMIT_PER_MINUTE_PER_USER,
)
from aos.api.shared.auth import require_login
from aos.api.shared.blocking import get_blocked_user_set
from aos.api.shared.rate_limit import rate_limit, rate_limit_key
from aos.api.shared.responses import ok
from aos.api.shared.user_display import get_user_display, get_user_display_map
from aos.services.chat.events import after_commit, publish_after_commit
from aos.services.chat.lock import authorize_locked_conversation
from aos.services.chat.membership import ACTIVE, DIRECT, active_member_users, require_active_membership


def _online(last_active) -> bool:
    return bool(last_active and time_diff_in_seconds(now_datetime(), last_active) <= ONLINE_THRESHOLD_SECONDS)


def _presence_payload(user: str) -> dict:
    display = get_user_display(user); last = frappe.db.get_value("User", user, "last_active")
    deleted = bool(display.get("is_deleted"))
    return {"account_id": display.get("account_id"), "display_name": display.get("display_name"), "avatar": display.get("avatar"),
            "is_live": bool(display.get("is_live")) if not deleted else False, "live_id": display.get("live_id") if not deleted else None,
            "live_status": display.get("live_status") if not deleted else None, "is_online": _online(last) if not deleted else False,
            "last_seen": None if deleted else last}


def _presence_payloads(users: list[str]) -> list[dict]:
    if not users: return []
    displays = get_user_display_map(users)
    last_rows = frappe.get_all("User", filters={"name":["in",users]}, fields=["name","last_active"], limit=len(users))
    lasts = {str(r.name):r.last_active for r in last_rows}
    out=[]
    for user in users:
        d=displays.get(user) or {}; deleted=bool(d.get("is_deleted")); last=lasts.get(user)
        out.append({"account_id":d.get("account_id"),"display_name":d.get("display_name"),"avatar":d.get("avatar"),
                    "is_live":bool(d.get("is_live")) if not deleted else False,"live_id":d.get("live_id") if not deleted else None,
                    "live_status":d.get("live_status") if not deleted else None,"is_online":_online(last) if not deleted else False,
                    "last_seen":None if deleted else last})
    return out


def touch_user_activity(user: str) -> None:
    if not user:return
    try:
        cache=frappe.cache(); key=rate_limit_key("chat","presence","activity_write",user)
        if not cache.set(key,"1",ex=PRESENCE_ACTIVITY_WRITE_THROTTLE_SECONDS,nx=True):return
        frappe.db.set_value("User",user,"last_active",now_datetime(),update_modified=False)
    except Exception:
        frappe.log_error("Chat presence activity update failed.","AOS Chat Presence")


def _presence_subscribers(user: str) -> list[str]:
    rows=frappe.db.sql("""
      SELECT peer.user AS peer, COALESCE(me.last_visible_message_at, me.joined_at) AS activity_at, me.conversation
      FROM `tabAOS Conversation Participant` me
      JOIN `tabAOS Conversation Participant` peer ON peer.conversation=me.conversation AND peer.status='active' AND peer.user!=me.user
      WHERE me.user=%(user)s AND me.status='active'
      ORDER BY activity_at DESC, me.conversation DESC LIMIT %(limit)s
    """,{"user":user,"limit":PRESENCE_MAX_PEERS*4},as_dict=True)
    peers=list(dict.fromkeys(str(r.peer) for r in rows if r.peer))[:PRESENCE_MAX_PEERS]
    blocked=get_blocked_user_set(user,peers) if peers else set()
    return [p for p in peers if p not in blocked]


def publish_presence_update(user: str, to_user: str | None=None):
    if not user:return
    payload=_presence_payload(user)
    if to_user:
        if to_user in get_blocked_user_set(user,[to_user]):return
        frappe.publish_realtime(event="aos_presence_update",message=payload,user=to_user);return
    cache=frappe.cache();key=rate_limit_key("chat","presence","broadcast",user)
    if not cache.set(key,"1",ex=PRESENCE_BROADCAST_THROTTLE_SECONDS,nx=True):return
    for peer in _presence_subscribers(user):
        frappe.publish_realtime(event="aos_presence_update",message=payload,user=peer)


def publish_presence_update_to_peers(user: str): publish_presence_update(user=user)

def schedule_presence_update_to_peers(user: str):
    touch_user_activity(user); after_commit(lambda: publish_presence_update(user=user))


def get_presence_impl(**kwargs):
    user,err=require_login()
    if err:return err
    limited=rate_limit(key=rate_limit_key("chat","presence","get",user),ttl_seconds=60,limit=GET_PRESENCE_LIMIT_PER_MINUTE_PER_USER,message="Too many presence requests. Please try again shortly.")
    if limited:return limited
    conv=kwargs.get("conversation_id");authorize_locked_conversation(user=user,conversation_id=conv,lock_token=kwargs.get("lock_token"))
    conversation,_=require_active_membership(conv,user); peers=active_member_users(conv,exclude_user=user)
    if conversation.conversation_type==DIRECT:
        blocked=get_blocked_user_set(user,peers) if peers else set(); peers=[p for p in peers if p not in blocked]
    return ok("Presence fetched.",data={"conversation_id":conv,"participants":_presence_payloads(peers)})


def send_typing_event_impl(**kwargs):
    user,err=require_login()
    if err:return err
    limited=rate_limit(key=rate_limit_key("chat","typing",user),ttl_seconds=60,limit=SEND_TYPING_LIMIT_PER_MINUTE_PER_USER,message="Too many typing events. Please slow down.")
    if limited:return limited
    conv=kwargs.get("conversation_id");authorize_locked_conversation(user=user,conversation_id=conv,lock_token=kwargs.get("lock_token"))
    conversation,_=require_active_membership(conv,user); recipients=active_member_users(conv,exclude_user=user)
    if conversation.conversation_type==DIRECT and recipients:
        blocked=get_blocked_user_set(user,recipients);recipients=[r for r in recipients if r not in blocked]
    from aos.services.accounts.identity import public_account_id_for_user
    payload={"conversation_id":conv,"account_id":public_account_id_for_user(user),"is_typing":bool(int(kwargs.get("is_typing") or 0))}
    for recipient in recipients: publish_after_commit(event="aos_typing",message=payload,user=recipient)
    touch_user_activity(user)
    return ok("Typing event sent.",data={"conversation_id":conv,"is_typing":payload["is_typing"]})
