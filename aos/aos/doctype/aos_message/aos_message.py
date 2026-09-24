# Copyright (c) 2026, Africa Online Stores and contributors

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.calls.identifiers import PUBLIC_CALL_ID_RE
from aos.services.chat.identifiers import generate_message_id
from aos.services.live.validation import LIVE_ID_RE
from aos.services.marketplace_discovery.ids import resolve_ad_name
from aos.services.shorts.identity import SHORT_ID_RE

VALID_MESSAGE_TYPES = {"text", "media", "ad", "short", "live", "mixed", "system"}
IMMUTABLE_REFERENCE_FIELDS = [
    "conversation", "sender", "message_type", "ad", "short", "live", "reply_to_message",
]
SYSTEM_MANAGED_FIELDS = [
    "is_forwarded", "forwarded_from_message", "forwarded_from_conversation", "is_edited", "edited_at",
    "original_content", "deleted_for_everyone", "deleted_for_everyone_at", "idempotency_key",
    "idempotency_request_hash", "call_id", "has_attachments", "recipient_count",
]


class AOSMessage(Document):
    def autoname(self):
        self.name = generate_message_id()

    def validate(self):
        self._validate_conversation()
        self._validate_type()
        self._validate_sender()
        self._validate_content()
        self._validate_references()
        self._validate_reply()
        self._validate_forwarding()

    def before_insert(self):
        self.is_forwarded = int(self.is_forwarded or 0)
        self.is_edited = int(self.is_edited or 0)
        self.deleted_for_everyone = int(self.deleted_for_everyone or 0)
        self.has_attachments = int(self.has_attachments or 0)
        self.recipient_count = max(0, int(self.recipient_count or 0))

    def before_save(self):
        if self.is_new():
            return
        original = frappe.db.get_value(self.doctype, self.name, IMMUTABLE_REFERENCE_FIELDS + SYSTEM_MANAGED_FIELDS, as_dict=True)
        if not original:
            return
        for fieldname in IMMUTABLE_REFERENCE_FIELDS + SYSTEM_MANAGED_FIELDS:
            if getattr(self, fieldname, None) != original.get(fieldname):
                frappe.throw(f"{fieldname} cannot be modified directly")

    def _validate_conversation(self):
        if not self.conversation or not frappe.db.exists("AOS Conversation", self.conversation):
            frappe.throw("Invalid conversation")

    def _validate_type(self):
        if self.message_type not in VALID_MESSAGE_TYPES:
            frappe.throw("Invalid message type")

    def _validate_sender(self):
        if not self.sender:
            frappe.throw("Sender is required")
        if self.message_type == "system":
            return
        if not frappe.db.exists(
            "AOS Conversation Participant",
            {"conversation": self.conversation, "user": self.sender, "status": "active"},
        ):
            frappe.throw("Sender must be an active conversation participant")

    def _validate_content(self):
        content = str(self.content or "").strip()
        refs = bool(self.ad or self.short or self.live)
        if self.message_type == "text" and not content:
            frappe.throw("Content is required for text messages")
        if self.message_type == "system" and not content:
            frappe.throw("Content is required for system messages")
        if self.message_type == "ad" and not self.ad:
            frappe.throw("Ad is required for ad messages")
        if self.message_type == "short" and not self.short:
            frappe.throw("Short is required for short messages")
        if self.message_type == "live" and not self.live:
            frappe.throw("Live is required for live messages")
        if self.message_type == "mixed" and not content and not refs and not int(self.has_attachments or 0):
            # Attachments are normally linked immediately after insert; API sets
            # has_attachments before insert for this validation path.
            frappe.throw("Mixed messages require content, a shared object, or attachments")
        self.content = content or None

    def _validate_references(self):
        if self.ad:
            try:
                resolve_ad_name(self.ad)
            except Exception:
                frappe.throw("Invalid ad reference")
        if self.short:
            value = str(self.short).strip()
            if not SHORT_ID_RE.fullmatch(value) or not frappe.db.exists("AOS Short", value):
                frappe.throw("Invalid short reference")
            self.short = value
        if self.live:
            value = str(self.live).strip()
            if not LIVE_ID_RE.fullmatch(value) or not frappe.db.exists("AOS Live Stream", value):
                frappe.throw("Invalid live reference")
            self.live = value
        if self.call_id:
            call_id = str(self.call_id).strip()
            if self.message_type != "system" or not PUBLIC_CALL_ID_RE.fullmatch(call_id):
                frappe.throw("Invalid call reference")
            if not frappe.db.exists("AOS Call", {"public_id": call_id}):
                frappe.throw("Invalid call reference")
            self.call_id = call_id

    def _validate_reply(self):
        if not self.reply_to_message:
            return
        row = frappe.db.get_value("AOS Message", self.reply_to_message, ["name", "conversation"], as_dict=True)
        if not row or row.conversation != self.conversation or row.name == self.name:
            frappe.throw("Invalid reply message")

    def _validate_forwarding(self):
        if not int(self.is_forwarded or 0):
            if self.forwarded_from_message or self.forwarded_from_conversation:
                frappe.throw("Forward provenance requires is_forwarded")
            return
        if not self.forwarded_from_message or not self.forwarded_from_conversation:
            frappe.throw("Forward provenance is required")
        source = frappe.db.get_value(
            "AOS Message", self.forwarded_from_message, ["name", "conversation"], as_dict=True
        )
        if not source or source.conversation != self.forwarded_from_conversation:
            frappe.throw("Invalid forward provenance")
