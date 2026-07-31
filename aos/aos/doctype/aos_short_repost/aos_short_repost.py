# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import hashlib

import frappe
from frappe.model.document import Document


class AOSShortRepost(Document):
    def before_insert(self):
        self._set_user()

    def validate(self):
        self._validate_status()
        self._validate_short()
        self._set_active_key()
        self._prevent_duplicate_active()

    def after_insert(self):
        if self.status == "active":
            self._increment_repost_count()
            self._record_repost_event()

    def on_update(self):
        if not self.has_value_changed("status"):
            return

        old_status = self.get_doc_before_save().status if self.get_doc_before_save() else None

        if old_status != "active" and self.status == "active":
            self._increment_repost_count()
            self._record_repost_event()
        elif old_status == "active" and self.status != "active":
            self._decrement_repost_count()

    def on_trash(self):
        if self.status == "active":
            self._decrement_repost_count()

    def _set_user(self):
        if not self.user:
            self.user = frappe.session.user

        if self.user == "Guest":
            frappe.throw("Login required to repost a short")

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["status", "visibility_status", "owner"],
            as_dict=True,
        )

        # Existing repost rows must be removable even after the original short
        # becomes unavailable. Only active repost creation/reactivation requires
        # the original short to be ready and visible.
        if self.status != "active":
            return

        if not short:
            frappe.throw("Short not found")

        if short.status != "ready":
            frappe.throw("Short is not available")

        if short.visibility_status != "visible":
            frappe.throw("Short is not visible")

        if short.owner == self.user:
            frappe.throw("You cannot repost your own short")

    def _validate_status(self):
        if not self.status:
            self.status = "active"

        if self.status not in {"active", "deleted"}:
            frappe.throw("Invalid repost status")

    def _set_active_key(self):
        if self.status == "active" and self.short and self.user:
            material = f"{self.short}|{self.user}".encode("utf-8")
            self.active_key = hashlib.sha256(material).hexdigest()
        else:
            self.active_key = None

    def _prevent_duplicate_active(self):
        if self.status != "active":
            return

        existing = frappe.db.exists(
            "AOS Short Repost",
            {
                "short": self.short,
                "user": self.user,
                "status": "active",
            },
        )

        if existing and existing != self.name:
            frappe.throw("Short already reposted")

    def _increment_repost_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET repost_count = repost_count + 1
            WHERE name = %s
            """,
            (self.short,),
        )

    def _decrement_repost_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET repost_count = GREATEST(repost_count - 1, 0)
            WHERE name = %s
            """,
            (self.short,),
        )

    def _record_repost_event(self):
        try:
            frappe.get_doc(
                {
                    "doctype": "AOS Short Event",
                    "short": self.short,
                    "user": self.user,
                    "event_type": "repost",
                }
            ).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Short Repost event failed")
