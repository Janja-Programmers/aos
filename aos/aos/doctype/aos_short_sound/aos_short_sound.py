# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSShortSound(Document):
    def validate(self):
        self._validate_short()
        self._validate_sound()
        self._validate_timing()
        self._prevent_duplicate_short_sound()

    def after_insert(self):
        self._increment_sound_usage()

    def on_update(self):
        if not self.has_value_changed("sound"):
            return

        previous = self.get_doc_before_save()
        if previous and previous.sound and previous.sound != self.sound:
            self._decrement_sound_usage(previous.sound)
            self._increment_sound_usage(self.sound)

    def on_trash(self):
        self._decrement_sound_usage(self.sound)

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        if not frappe.db.exists("AOS Short", self.short):
            frappe.throw("Short not found")

    def _validate_sound(self):
        if not self.sound:
            frappe.throw("Sound is required")

        sound = frappe.db.get_value(
            "AOS Sound",
            self.sound,
            ["status"],
            as_dict=True,
        )

        if not sound:
            frappe.throw("Sound not found")

        if sound.status != "active":
            frappe.throw("Sound is not active")

    def _validate_timing(self):
        try:
            self.start_ms = int(self.start_ms or 0)
            self.duration_ms = int(self.duration_ms or 0)
            self.volume = float(self.volume if self.volume is not None else 1.0)
        except Exception:
            frappe.throw("Invalid sound timing")

        if self.start_ms < 0:
            frappe.throw("Start time cannot be negative")

        if self.duration_ms < 0:
            frappe.throw("Duration cannot be negative")

        if self.volume < 0 or self.volume > 1:
            frappe.throw("Volume must be between 0 and 1")

        self.is_original_audio = 1 if int(self.is_original_audio or 0) else 0

    def _prevent_duplicate_short_sound(self):
        existing = frappe.db.exists("AOS Short Sound", {"short": self.short})
        if existing and existing != self.name:
            frappe.throw("Short already has a sound")

    def _increment_sound_usage(self, sound=None):
        sound = sound or self.sound
        if not sound:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Sound`
            SET usage_count = usage_count + 1
            WHERE name = %s
            """,
            (sound,),
        )

    def _decrement_sound_usage(self, sound=None):
        sound = sound or self.sound
        if not sound:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Sound`
            SET usage_count = GREATEST(usage_count - 1, 0)
            WHERE name = %s
            """,
            (sound,),
        )
