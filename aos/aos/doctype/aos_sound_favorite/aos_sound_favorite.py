# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSSoundFavorite(Document):
    def before_insert(self):
        self._set_user()

    def validate(self):
        self._validate_sound()
        self._prevent_duplicate()

    def after_insert(self):
        self._increment_favorite_count()

    def on_trash(self):
        self._decrement_favorite_count()

    def _set_user(self):
        if not self.user:
            self.user = frappe.session.user

        if self.user == "Guest":
            frappe.throw("Login required to favorite a sound")

    def _validate_sound(self):
        if not self.sound:
            frappe.throw("Sound is required")

        sound = frappe.db.get_value("AOS Sound", self.sound, ["status"], as_dict=True)
        if not sound:
            frappe.throw("Sound not found")

        if sound.status != "active":
            frappe.throw("Sound is not active")

    def _prevent_duplicate(self):
        existing = frappe.db.exists("AOS Sound Favorite", {"sound": self.sound, "user": self.user})
        if existing and existing != self.name:
            frappe.throw("Sound already favorited")

    def _increment_favorite_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Sound`
            SET favorite_count = favorite_count + 1
            WHERE name = %s
            """,
            (self.sound,),
        )

    def _decrement_favorite_count(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Sound`
            SET favorite_count = GREATEST(favorite_count - 1, 0)
            WHERE name = %s
            """,
            (self.sound,),
        )
