"""One-time reconciliation from the hardened direct-only Chat schema to normalized membership/state.

Fresh sites have no rows and simply pass through. Upgraded sites are backfilled before
legacy participant/message columns are removed.
"""
from __future__ import annotations

import frappe
from frappe.utils import now_datetime
from aos.services.chat.identifiers import generate_message_state_id
from aos.services.chat.membership import direct_key

LEGACY_CONVERSATION_COLUMNS=("participant_1","participant_2","pair_key","unread_count_1","unread_count_2","is_active_1","is_active_2","last_message_1","last_message_at_1","last_sender_1","last_message_2","last_message_at_2","last_sender_2")
LEGACY_MESSAGE_COLUMNS=("deleted_for_1","deleted_for_1_at","deleted_for_2","deleted_for_2_at","delivered_to_receiver_at","read_by_receiver_at")


def _has(dt,field): return frappe.db.table_exists(dt) and frappe.db.has_column(dt,field)

def _ensure_participant(conv,user,*,unread,active,last_message,last_at,last_sender,creation):
    if not user:return
    existing=frappe.db.get_value("AOS Conversation Participant",{"conversation":conv,"user":user},"name")
    values={"role":"member","status":"active" if active else "left","joined_at":creation,"visible_from":creation,"left_at":None if active else now_datetime(),"added_by":user,"unread_count":int(unread or 0),"is_hidden":0 if active else 1,"last_visible_message":last_message,"last_visible_message_at":last_at,"last_visible_sender":last_sender}
    if existing: frappe.db.set_value("AOS Conversation Participant",existing,values,update_modified=False);return
    doc=frappe.new_doc("AOS Conversation Participant");doc.conversation=conv;doc.user=user
    for k,v in values.items():setattr(doc,k,v)
    doc.insert(ignore_permissions=True)


def _state(mid,conv,user,*,hidden_at=None,delivered_at=None,read_at=None):
    if not user or not any((hidden_at,delivered_at,read_at)):return
    existing=frappe.db.get_value("AOS Message User State",{"message":mid,"user":user},"name")
    vals={"hidden_at":hidden_at,"delivered_at":delivered_at,"read_at":read_at}
    if existing: frappe.db.set_value("AOS Message User State",existing,vals,update_modified=False);return
    doc=frappe.new_doc("AOS Message User State");doc.name=generate_message_state_id();doc.message=mid;doc.conversation=conv;doc.user=user
    for k,v in vals.items():setattr(doc,k,v)
    doc.insert(ignore_permissions=True)


def _backfill():
    if not _has("AOS Conversation","participant_1"):return
    conversations=frappe.db.sql("""SELECT name,participant_1,participant_2,pair_key,unread_count_1,unread_count_2,is_active_1,is_active_2,last_message_1,last_message_at_1,last_sender_1,last_message_2,last_message_at_2,last_sender_2,creation FROM `tabAOS Conversation` ORDER BY creation,name""",as_dict=True)
    for c in conversations:
        p1,p2=str(c.participant_1 or ""),str(c.participant_2 or "")
        key=direct_key(p1,p2) if p1 and p2 and p1!=p2 else None
        latest_at=max([x for x in (c.last_message_at_1,c.last_message_at_2) if x],default=None)
        latest_message=c.last_message_1 if c.last_message_at_1==latest_at else c.last_message_2
        latest_sender=c.last_sender_1 if c.last_message_at_1==latest_at else c.last_sender_2
        frappe.db.set_value("AOS Conversation",c.name,{"conversation_type":"direct","direct_key":key,"created_by":p1 or p2,"participant_count":sum(bool(x) for x in (p1,p2)),"last_message":latest_message,"last_message_at":latest_at,"last_sender":latest_sender},update_modified=False)
        _ensure_participant(c.name,p1,unread=c.unread_count_1,active=int(c.is_active_1 or 0),last_message=c.last_message_1,last_at=c.last_message_at_1,last_sender=c.last_sender_1,creation=c.creation)
        _ensure_participant(c.name,p2,unread=c.unread_count_2,active=int(c.is_active_2 or 0),last_message=c.last_message_2,last_at=c.last_message_at_2,last_sender=c.last_sender_2,creation=c.creation)
    if _has("AOS Message","deleted_for_1"):
        messages=frappe.db.sql("""SELECT m.name,m.conversation,m.sender,m.deleted_for_1,m.deleted_for_1_at,m.deleted_for_2,m.deleted_for_2_at,m.delivered_to_receiver_at,m.read_by_receiver_at,c.participant_1,c.participant_2 FROM `tabAOS Message` m JOIN `tabAOS Conversation` c ON c.name=m.conversation ORDER BY m.creation,m.name""",as_dict=True)
        for m in messages:
            p1,p2=m.participant_1,m.participant_2
            receiver=p2 if m.sender==p1 else p1 if m.sender==p2 else None
            _state(m.name,m.conversation,p1,hidden_at=m.deleted_for_1_at if int(m.deleted_for_1 or 0) else None,delivered_at=m.delivered_to_receiver_at if receiver==p1 else None,read_at=m.read_by_receiver_at if receiver==p1 else None)
            _state(m.name,m.conversation,p2,hidden_at=m.deleted_for_2_at if int(m.deleted_for_2 or 0) else None,delivered_at=m.delivered_to_receiver_at if receiver==p2 else None,read_at=m.read_by_receiver_at if receiver==p2 else None)
            frappe.db.set_value("AOS Message",m.name,"recipient_count",1 if receiver else 0,update_modified=False)


def _drop_columns(doctype,columns):
    table=f"tab{doctype}"
    for field in columns:
        if _has(doctype,field): frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{field}`")


def execute():
    _backfill()
    _drop_columns("AOS Message",LEGACY_MESSAGE_COLUMNS)
    _drop_columns("AOS Conversation",LEGACY_CONVERSATION_COLUMNS)
