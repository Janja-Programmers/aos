from __future__ import annotations

from frappe.model.document import Document


class AOSNotificationDeliveryJob(Document):
    def validate(self):
        if not self.status:
            self.status = "Queued"
        if not self.channel:
            self.channel = "push"
        if not self.delivery_kind:
            self.delivery_kind = "persistent"
        if not self.attempt_count:
            self.attempt_count = 0
        if not self.max_attempts:
            self.max_attempts = 3
        if not self.success_count:
            self.success_count = 0
        if not self.failure_count:
            self.failure_count = 0
        if not self.inactive_count:
            self.inactive_count = 0
        if not self.token_count:
            self.token_count = 0
