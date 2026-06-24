# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortMention(Document):
    def validate(self):
        self._validate_short()
        self._validate_comment()
        self._validate_users()
        self._validate_source_type()
        self._prevent_duplicate()

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        if not frappe.db.exists("AOS Short", self.short):
            frappe.throw("Invalid short")

    def _validate_comment(self):
        if not self.comment:
            return

        comment_short = frappe.db.get_value("AOS Short Comment", self.comment, "short")
        if not comment_short:
            frappe.throw("Invalid comment")

        if comment_short != self.short:
            frappe.throw("Mention comment does not belong to this short")

    def _validate_users(self):
        if not self.mentioned_user:
            frappe.throw("Mentioned user is required")

        if not self.mentioned_by:
            frappe.throw("Mentioned by is required")

        if not frappe.db.exists("User", self.mentioned_user):
            frappe.throw("Invalid mentioned user")

        if not frappe.db.exists("User", self.mentioned_by):
            frappe.throw("Invalid mentioning user")

    def _validate_source_type(self):
        if self.source_type not in {"caption", "comment", "reply"}:
            frappe.throw("Invalid mention source type")

        if self.source_type == "caption" and self.comment:
            frappe.throw("Caption mentions cannot reference a comment")

        if self.source_type in {"comment", "reply"} and not self.comment:
            frappe.throw("Comment mention requires a comment")

    def _prevent_duplicate(self):
        filters = {
            "short": self.short,
            "comment": self.comment,
            "mentioned_user": self.mentioned_user,
            "source_type": self.source_type,
        }

        existing = frappe.db.exists("AOS Short Mention", filters)
        if existing and existing != self.name:
            frappe.throw("Duplicate mention")
