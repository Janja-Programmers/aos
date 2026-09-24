# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from aos.services.chat.identifiers import generate_attachment_id


class AOSMessageAttachment(Document):
    """Chat-owned linkage between a message and hardened Media.

    Content type, filename, dimensions, duration, visibility, URLs and storage
    metadata remain Media-owned and are projected at read time.
    """

    def autoname(self):
        self.name = generate_attachment_id()

    def validate(self):
        self._validate_message()
        self._validate_media()
        self._validate_attachment_count()
        self._prevent_duplicates()
        self._set_sort_order()

    def after_insert(self):
        self._mark_message_has_attachments()

    def on_trash(self):
        self._sync_message_has_attachments_on_delete()

    def _validate_message(self):
        if not self.message or not frappe.db.exists("AOS Message", self.message):
            frappe.throw("Invalid message")

    def _validate_media(self):
        if not self.media:
            frappe.throw("Attachment media is required")
        media = frappe.db.get_value(
            "AOS Media Object",
            self.media,
            ["name", "purpose", "status", "visibility", "attached_doctype", "attached_name"],
            as_dict=True,
        )
        if not media:
            frappe.throw("Invalid media")
        if media.purpose != "chat_attachment" or media.visibility != "Private":
            frappe.throw("Invalid media for chat attachment")
        if media.status not in {"Uploaded", "Attached"}:
            frappe.throw("Media must be ready before it can be attached")
        # A forwarded message may link to the same immutable private Media
        # object. Authorization is determined from Chat linkage rows; Media's
        # primary attached_name remains the original attachment owner.

    def _validate_attachment_count(self):
        if not self.is_new():
            return
        from aos.services.chat.validation import MAX_ATTACHMENTS

        existing = frappe.db.count("AOS Message Attachment", {"message": self.message})
        if int(existing or 0) >= MAX_ATTACHMENTS:
            frappe.throw(f"A message can contain at most {MAX_ATTACHMENTS} attachments")

    def _prevent_duplicates(self):
        if self.is_new() and frappe.db.exists(
            "AOS Message Attachment", {"message": self.message, "media": self.media}
        ):
            frappe.throw("This media is already attached to the message")

    def _set_sort_order(self):
        if self.sort_order is not None and int(self.sort_order or 0) >= 0:
            return
        self.sort_order = 0

    def _mark_message_has_attachments(self):
        frappe.db.set_value("AOS Message", self.message, "has_attachments", 1, update_modified=False)

    def _sync_message_has_attachments_on_delete(self):
        remaining = frappe.db.count("AOS Message Attachment", {"message": self.message})
        if remaining <= 1:
            frappe.db.set_value("AOS Message", self.message, "has_attachments", 0, update_modified=False)
