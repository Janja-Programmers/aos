"""Install canonical normalized Chat uniqueness and hot-query indexes."""
from __future__ import annotations
from collections.abc import Sequence
import frappe

INDEXES: tuple[tuple[str,str,tuple[str,...],bool], ...] = (
    ("AOS Conversation","uniq_chat_direct_key",("direct_key",),True),
    ("AOS Conversation","idx_chat_conversation_activity",("last_message_at","name"),False),
    ("AOS Conversation Participant","uniq_chat_participant",("conversation","user"),True),
    ("AOS Conversation Participant","idx_chat_participant_inbox",("user","status","is_hidden","is_locked","last_visible_message_at","name"),False),
    ("AOS Conversation Participant","idx_chat_participant_members",("conversation","status","joined_at","name"),False),
    ("AOS Conversation Participant","idx_chat_participant_role",("conversation","status","role","joined_at","name"),False),
    ("AOS Message","uniq_chat_message_idempotency",("idempotency_key",),True),
    ("AOS Message","uniq_chat_call_message",("call_id",),True),
    ("AOS Message","idx_chat_message_history",("conversation","creation","name"),False),
    ("AOS Message","idx_chat_message_sender",("sender","conversation","creation","name"),False),
    ("AOS Message","idx_chat_message_reply",("reply_to_message","creation","name"),False),
    ("AOS Message","idx_chat_message_ad",("ad","creation","name"),False),
    ("AOS Message","idx_chat_message_short",("short","creation","name"),False),
    ("AOS Message","idx_chat_message_live",("live","creation","name"),False),
    ("AOS Message User State","uniq_chat_message_user_state",("message","user"),True),
    ("AOS Message User State","idx_chat_state_user_read",("conversation","user","read_at","message"),False),
    ("AOS Message User State","idx_chat_state_user_delivery",("conversation","user","delivered_at","message"),False),
    ("AOS Message User State","idx_chat_state_user_hidden",("conversation","user","hidden_at","message"),False),
    ("AOS Message Attachment","uniq_chat_attachment_message_media",("message","media"),True),
    ("AOS Message Attachment","idx_chat_attachment_message",("message","sort_order","name"),False),
    ("AOS Message Star","uniq_chat_star_message_user",("message","user"),True),
    ("AOS Message Star","idx_chat_star_user_created",("user","creation","name"),False),
    ("AOS Message Reaction","uniq_chat_reaction_message_user",("message","user"),True),
    ("AOS Message Reaction","idx_chat_reaction_message",("message","emoji","user"),False),
    ("AOS Message Translation","uniq_chat_translation_request_cache",("message","request_source_language","request_target_language","original_content_hash"),True),
    ("AOS Message Translation","idx_chat_translation_conversation",("conversation","creation","name"),False),
    ("AOS Chat Lock Credential","uniq_chat_lock_user",("user",),True),
)


def execute():
    for doctype,name,columns,unique in INDEXES: _ensure_index(doctype,name,columns,unique=unique)

def _table(doctype): return f"tab{doctype}"

def _exists(doctype,name):
    return bool(frappe.db.sql("SELECT 1 FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1",(_table(doctype),name)))

def _ensure_columns(doctype,columns:Sequence[str]):
    if not frappe.db.table_exists(doctype): frappe.throw(f"Chat schema table is missing: {doctype}")
    missing=[c for c in columns if not frappe.db.has_column(doctype,c)]
    if missing: frappe.throw(f"Chat schema columns are missing for {doctype}: {', '.join(missing)}")

def _assert_unique_ready(doctype,columns,name):
    non_null=" AND ".join(f"`{c}` IS NOT NULL AND `{c}` != ''" for c in columns); groups=", ".join(f"`{c}`" for c in columns)
    if frappe.db.sql(f"SELECT 1 FROM `{_table(doctype)}` WHERE {non_null} GROUP BY {groups} HAVING COUNT(*)>1 LIMIT 1"):
        frappe.throw(f"Cannot install Chat unique index {name}; duplicate data remains in {doctype}")

def _ensure_index(doctype,name,columns,*,unique):
    _ensure_columns(doctype,columns)
    if _exists(doctype,name): return
    if unique:_assert_unique_ready(doctype,columns,name)
    if unique: frappe.db.add_unique(doctype,list(columns),constraint_name=name)
    else: frappe.db.add_index(doctype,list(columns),index_name=name)
