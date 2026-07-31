# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document


class AOSVideoProcessingJob(Document):
    def validate(self):
        if self.attempt_count is None:
            self.attempt_count = 0
        if not self.max_attempts:
            self.max_attempts = 3
        if not self.status:
            self.status = "Queued"
        if not self.reason:
            self.reason = "short_upload"
        self.generation = max(1, int(self.generation or 1))
        if self.status in {"Queued", "Dispatching", "Processing"} and self.short:
            self.active_key = hashlib.sha256(str(self.short).encode("utf-8")).hexdigest()
        else:
            self.active_key = None

        if self.short and not self.raw_video_media:
            self.raw_video_media = frappe.db.get_value("AOS Short", self.short, "raw_video_media")
