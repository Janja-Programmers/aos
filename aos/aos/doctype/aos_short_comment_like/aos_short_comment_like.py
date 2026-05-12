# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortCommentLike(Document):
    def validate(self):
        self._set_user()
        self._set_short_from_comment()
        self._validate_comment()
        self._validate_duplicate()

    def after_insert(self):
        self._increment_like_count()

    def on_trash(self):
        self._decrement_like_count()

    # PRIVATE METHODS
    def _set_user(self):
        """Ensure like is tied to logged-in user."""
        if not self.user:
            self.user = frappe.session.user

        if not self.user or self.user == "Guest":
            frappe.throw("Login required to like a comment")

    def _set_short_from_comment(self):
        """Resolve parent short from linked comment."""
        if not self.comment:
            frappe.throw("Comment is required")

        short = frappe.db.get_value(
            "AOS Short Comment",
            self.comment,
            "short",
        )

        if not short:
            frappe.throw("Comment not found")

        self.short = short

    def _validate_comment(self):
        """Ensure comment exists, is active, and belongs to a visible short."""
        comment = frappe.db.get_value(
            "AOS Short Comment",
            self.comment,
            ["name", "short", "status"],
            as_dict=True,
        )

        if not comment:
            frappe.throw("Comment not found")

        if comment.status != "active":
            frappe.throw("Comment is not available")

        short = frappe.db.get_value(
            "AOS Short",
            comment.short,
            ["name", "status", "visibility_status"],
            as_dict=True,
        )

        if not short:
            frappe.throw("Short not found")

        if short.status != "ready":
            frappe.throw("Short is not available")

        if short.visibility_status != "visible":
            frappe.throw("Short is not visible")

    def _validate_duplicate(self):
        """Prevent same user from liking the same comment more than once."""
        existing = frappe.db.exists(
            "AOS Short Comment Like",
            {
                "comment": self.comment,
                "user": self.user,
            },
        )

        if existing and existing != self.name:
            frappe.throw("Comment already liked")

    def _increment_like_count(self):
        """Atomic increment."""
        frappe.db.sql(
            """
            UPDATE `tabAOS Short Comment`
            SET like_count = COALESCE(like_count, 0) + 1
            WHERE name = %s
            """,
            (self.comment,),
        )

    def _decrement_like_count(self):
        """Atomic decrement, clamped at zero."""
        frappe.db.sql(
            """
            UPDATE `tabAOS Short Comment`
            SET like_count = GREATEST(COALESCE(like_count, 0) - 1, 0)
            WHERE name = %s
            """,
            (self.comment,),
        )
