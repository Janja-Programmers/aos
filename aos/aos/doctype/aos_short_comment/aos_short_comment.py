# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortComment(Document):
    def validate(self):
        self._set_user()
        self._validate_short()
        self._validate_body()
        self._validate_parent()

    def before_insert(self):
        self._set_seller()
        self._set_thread_fields()

    def after_insert(self):
        # Fix root for top-level
        if not self.parent_comment:
            frappe.db.set_value(
                self.doctype,
                self.name,
                "root_comment",
                self.name,
                update_modified=False,
            )

        self._increment_short_comment_count()
        self._increment_reply_count_if_needed()

    def soft_delete(self):
        """Soft delete comment"""
        if self.status == "deleted":
            return

        self.db_set("status", "deleted", update_modified=False)

        self._decrement_short_comment_count()
        self._decrement_reply_count_if_needed()

    def _set_user(self):
        if not self.user:
            self.user = frappe.session.user

        if self.user == "Guest":
            frappe.throw("Login required to comment")

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["status", "visibility_status"],
            as_dict=True,
        )

        if not short:
            frappe.throw("Short not found")

        if short.status != "ready":
            frappe.throw("Short is not available")

        if short.visibility_status != "visible":
            frappe.throw("Short is not visible")

    def _validate_body(self):
        if not self.comment:
            frappe.throw("Comment cannot be empty")

        self.comment = self.comment.strip()

        if not self.comment:
            frappe.throw("Comment cannot be empty")

        if len(self.comment) > 500:
            frappe.throw("Comment cannot exceed 500 characters")

    def _validate_parent(self):
        if not self.parent_comment:
            return

        parent = frappe.db.get_value(
            "AOS Short Comment",
            self.parent_comment,
            ["short", "root_comment", "status"],
            as_dict=True,
        )

        if not parent:
            frappe.throw("Parent comment not found")

        if parent.short != self.short:
            frappe.throw("Invalid parent comment")

        if parent.status != "active":
            frappe.throw("Cannot reply to this comment")

    def _set_seller(self):
        seller = frappe.db.get_value(
            "AOS Seller",
            {"user": self.user},
            "name",
        )
        if seller:
            self.seller = seller

    def _set_thread_fields(self):
        if not self.parent_comment:
            self.root_comment = None
        else:
            parent = frappe.get_doc("AOS Short Comment", self.parent_comment)
            self.root_comment = parent.root_comment or parent.name

    def _increment_short_comment_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET comment_count = comment_count + 1
            WHERE name = %s
            """,
            (self.short,),
        )

    def _increment_reply_count_if_needed(self):
        if not self.parent_comment:
            return

        root = self.root_comment or self.parent_comment

        frappe.db.sql(
            """
            UPDATE `tabAOS Short Comment`
            SET reply_count = reply_count + 1
            WHERE name = %s
            """,
            (root,),
        )

    def _decrement_short_comment_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET comment_count = GREATEST(comment_count - 1, 0)
            WHERE name = %s
            """,
            (self.short,),
        )

    def _decrement_reply_count_if_needed(self):
        if not self.parent_comment:
            return

        root = self.root_comment or self.parent_comment

        frappe.db.sql(
            """
            UPDATE `tabAOS Short Comment`
            SET reply_count = GREATEST(reply_count - 1, 0)
            WHERE name = %s
            """,
            (root,),
        )
