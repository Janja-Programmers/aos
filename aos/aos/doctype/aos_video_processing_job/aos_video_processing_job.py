# Copyright (c) 2026, Africa Online Stores and contributors
from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.shorts.constants import VIDEO_OPERATIONS


class AOSVideoProcessingJob(Document):
    def validate(self):
        if self.operation not in set(VIDEO_OPERATIONS):
            frappe.throw("Invalid video processing operation")
        if self.status not in {"Queued", "Processing", "Retry Waiting", "Ready", "Failed", "Cancelled"}:
            frappe.throw("Invalid video processing state")
        if int(self.max_attempts or 0) < 1 or int(self.max_attempts or 0) > 10:
            frappe.throw("Invalid video processing retry limit")
        if int(self.attempt_count or 0) < 0:
            frappe.throw("Invalid processing attempt count")
        if int(self.generation or 0) < 1:
            frappe.throw("Invalid processing generation")
        if self.next_retry_at and self.status != "Retry Waiting":
            self.next_retry_at = None
        self.last_error_code = str(self.last_error_code or "").strip()[:64]
        self.lease_owner = str(self.lease_owner or "").strip()[:140]
