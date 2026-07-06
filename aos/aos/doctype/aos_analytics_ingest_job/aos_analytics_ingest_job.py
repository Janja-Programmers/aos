from __future__ import annotations

from frappe.model.document import Document


class AOSAnalyticsIngestJob(Document):
    def validate(self):
        if not self.status:
            self.status = "Queued"
        if not self.source:
            self.source = "server"
        if not self.event_count:
            self.event_count = 0
        if not self.ingested_count:
            self.ingested_count = 0
        if not self.skipped_count:
            self.skipped_count = 0
        if not self.attempt_count:
            self.attempt_count = 0
        if not self.max_attempts:
            self.max_attempts = 3
