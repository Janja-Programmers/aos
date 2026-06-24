# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortSave(Document):
    def before_insert(self):
        self._set_user()

    def validate(self):
        self._validate_short()
        self._prevent_duplicate()

    def after_insert(self):
        self._increment_save_count()
        self._record_save_event()

    def on_trash(self):
        self._decrement_save_count()

    def _set_user(self):
        if not self.user:
            self.user = frappe.session.user

        if self.user == "Guest":
            frappe.throw("Login required to save a short")

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

    def _prevent_duplicate(self):
        existing = frappe.db.exists(
            "AOS Short Save",
            {"short": self.short, "user": self.user},
        )

        if existing and existing != self.name:
            frappe.throw("Short already saved")

    def _increment_save_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET save_count = save_count + 1
            WHERE name = %s
            """,
            (self.short,),
        )

    def _decrement_save_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET save_count = GREATEST(save_count - 1, 0)
            WHERE name = %s
            """,
            (self.short,),
        )

    def _record_save_event(self):
        try:
            frappe.get_doc(
                {
                    "doctype": "AOS Short Event",
                    "short": self.short,
                    "user": self.user,
                    "event_type": "save",
                }
            ).insert(ignore_permissions=True)
        except Exception:
            # Saving should not fail because analytics event recording failed.
            frappe.log_error(frappe.get_traceback(), "AOS Short Save event failed")
