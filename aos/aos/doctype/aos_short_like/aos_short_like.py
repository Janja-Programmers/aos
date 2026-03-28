# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortLike(Document):
    def validate(self):
        self._validate_short()

    def before_insert(self):
        self._set_user()

    def after_insert(self):
        self._increment_like_count()

    def on_trash(self):
        self._decrement_like_count()

    def _set_user(self):
        """Ensure like is tied to logged-in user"""
        if not self.user:
            self.user = frappe.session.user

        if self.user == "Guest":
            frappe.throw("Login required to like a short")

    def _validate_short(self):
        """Ensure short exists and is valid"""
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

    def _increment_like_count(self):
        """Atomic increment"""
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET like_count = like_count + 1
            WHERE name = %s
            """,
            (self.short,),
        )

    def _decrement_like_count(self):
        """Atomic decrement (safe)"""
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET like_count = GREATEST(like_count - 1, 0)
            WHERE name = %s
            """,
            (self.short,),
        )
