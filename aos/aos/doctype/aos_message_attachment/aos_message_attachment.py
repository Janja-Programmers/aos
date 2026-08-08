# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSMessageAttachment(Document):
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
        if not self.message:
            frappe.throw("Message is required")

        if not frappe.db.exists("AOS Message", self.message):
            frappe.throw("Invalid message")

    def _validate_media(self):
        if not self.media:
            frappe.throw("Attachment media is required")

        media = frappe.db.get_value(
            "AOS Media Object",
            self.media,
            ["name", "purpose", "status", "visibility", "content_type"],
            as_dict=True,
        )

        if not media:
            frappe.throw("Invalid media")

        if media.purpose != "chat_attachment":
            frappe.throw("Invalid media purpose for chat attachment")

        if media.visibility != "Private":
            frappe.throw("Chat attachments must use private media")

        if media.status not in {"Uploaded", "Attached"}:
            frappe.throw("Media must be uploaded before it can be attached")

        if not self.file_type:
            self.file_type = self._infer_file_type_from_content_type(media.content_type)


    def _validate_attachment_count(self):
        if not self.is_new():
            return

        # Public APIs hold the message/conversation lock while attaching. This
        # DocType guard protects Desk/import/internal writes from unbounded rows.
        from aos.services.chat.validation import MAX_ATTACHMENTS

        existing = frappe.db.count(
            "AOS Message Attachment",
            {"message": self.message},
        )
        if int(existing or 0) >= MAX_ATTACHMENTS:
            frappe.throw(f"A message can contain at most {MAX_ATTACHMENTS} attachments")

        if self.file_type and str(self.file_type).strip().lower() not in {
            "image",
            "video",
            "audio",
            "document",
        }:
            frappe.throw("Invalid attachment file type")

    def _prevent_duplicates(self):
        if not self.is_new():
            return

        filters = {"message": self.message, "media": self.media}

        exists = frappe.db.exists("AOS Message Attachment", filters)
        if exists:
            frappe.throw("This media is already attached to the message")

    def _set_sort_order(self):
        if self.sort_order is not None and self.sort_order != 0:
            return

        max_order = frappe.db.sql(
            """
            SELECT MAX(sort_order)
            FROM `tabAOS Message Attachment`
            WHERE message = %s
            """,
            self.message,
        )[0][0]

        self.sort_order = (max_order or 0) + 1

    def _infer_file_type_from_content_type(self, content_type):
        content_type = str(content_type or "").split(";", 1)[0].strip().lower()

        if content_type.startswith("image/"):
            return "image"
        if content_type.startswith("video/"):
            return "video"
        if content_type.startswith("audio/"):
            return "audio"
        return "document"

    def _infer_file_type(self, file_type):
        if not file_type:
            return "document"

        file_type = file_type.lower()

        if file_type in ["jpg", "jpeg", "png", "webp", "gif"]:
            return "image"
        elif file_type in ["mp4", "mov", "avi", "mkv"]:
            return "video"
        elif file_type in ["mp3", "wav", "ogg"]:
            return "audio"
        else:
            return "document"

    def _mark_message_has_attachments(self):
        frappe.db.set_value(
            "AOS Message",
            self.message,
            "has_attachments",
            1,
            update_modified=False,
        )

    def _sync_message_has_attachments_on_delete(self):
        remaining = frappe.db.count(
            "AOS Message Attachment",
            {"message": self.message},
        )

        if remaining <= 1:
            frappe.db.set_value(
                "AOS Message",
                self.message,
                "has_attachments",
                0,
                update_modified=False,
            )
