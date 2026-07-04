from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSSearchIndexJob(Document):
    def validate(self):
        if not self.status:
            self.status = "Queued"
        if not self.action:
            self.action = "upsert"
        if not self.attempt_count:
            self.attempt_count = 0
        if not self.max_attempts:
            self.max_attempts = 3
