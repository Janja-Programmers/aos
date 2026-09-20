# Copyright (c) 2026, Africa Online Stores and contributors
from __future__ import annotations
import frappe
from frappe.model.document import Document
from aos.services.shorts.identity import generate_sound_id

class AOSSound(Document):
    def autoname(self): self.name = generate_sound_id()
    def validate(self):
        if self.source_type not in {"library", "uploaded", "original"}: frappe.throw("Invalid Sound source type")
        if self.status not in {"active", "disabled", "takedown"}: frappe.throw("Invalid Sound status")
        if self.duration_seconds and float(self.duration_seconds) > 600: frappe.throw("Sound is too long")
        if self.available_from and self.available_until and self.available_from >= self.available_until: frappe.throw("Invalid Sound availability window")
