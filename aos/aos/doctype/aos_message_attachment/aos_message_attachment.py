# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSMessageAttachment(Document):
    def validate(self):
        self._validate_message()
        self._validate_file()
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

    def _validate_file(self):
        if not self.file:
            frappe.throw("File is required")

        file_doc = frappe.db.get_value(
            "File",
            self.file,
            ["file_url", "file_type"],
            as_dict=True,
        )

        if not file_doc:
            frappe.throw("Invalid file")

        # Optional: auto-detect file_type if not set
        if not self.file_type:
            self.file_type = self._infer_file_type(file_doc.file_type)

    def _prevent_duplicates(self):
        if self.is_new():
            exists = frappe.db.exists(
                "AOS Message Attachment",
                {
                    "message": self.message,
                    "file": self.file,
                },
            )
            if exists:
                frappe.throw("This file is already attached to the message")

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
