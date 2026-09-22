from __future__ import annotations
import hashlib
import frappe
from frappe.model.document import Document
from frappe.utils import getdate, now_datetime
from aos.services.shorts.analytics import bounded_watch_ms, qualifies_view
from aos.services.shorts.policy import can_view



class AOSShortView(Document):
    def validate(self):
        self.view_date = self.view_date or getdate()
        if not self.user and not self.session_id:
            frappe.throw("User or session_id is required")
        actor_key = f"user:{self.user}" if self.user else f"session:{self.session_id}"
        self.identity_key = hashlib.sha256(f"{self.short}|{actor_key}".encode()).hexdigest()
        short = frappe.db.get_value(
            "AOS Short", self.short,
            ["name", "owner", "lifecycle_status", "processing_status", "moderation_status", "audience", "duration_seconds"],
            as_dict=True,
        )
        viewer = self.user if self.user and self.user != "Guest" else None
        if not short or not can_view(short, viewer=viewer):
            frappe.throw("Short is not available")
        self._short_duration = float(short.duration_seconds or 0)
        self.watch_ms = bounded_watch_ms(self.watch_ms, duration_seconds=self._short_duration)

    def on_update(self):
        if self.qualified or not self.watch_ms:
            return
        if not qualifies_view(self.watch_ms, duration_seconds=self._short_duration):
            return
        frappe.db.set_value(self.doctype, self.name, "qualified", 1, update_modified=False)
        frappe.db.sql("UPDATE `tabAOS Short` SET view_count=COALESCE(view_count,0)+1,last_engagement_at=%s WHERE name=%s", (now_datetime(), self.short))
