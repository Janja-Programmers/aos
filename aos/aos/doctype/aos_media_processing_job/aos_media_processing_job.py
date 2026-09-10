# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

from aos.services.media.media_purposes import get_media_purpose

_VALID_STATUSES = {"Queued", "Processing", "Retry Waiting", "Succeeded", "Failed"}
_VALID_OPERATIONS = {"Background Removal"}
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class AOSMediaProcessingJob(Document):
    """Durable state for asynchronous Media-owned processing work."""

    def validate(self):
        self.operation = str(self.operation or "").strip()
        self.status = str(self.status or "Queued").strip()
        self.result_purpose = str(self.result_purpose or "").strip()
        self.request_key = str(self.request_key or "").strip().lower()
        self.last_error_code = str(self.last_error_code or "").strip()[:64]
        self.last_error_message = str(self.last_error_message or "").strip()[:280]
        self.attempt_count = max(0, int(self.attempt_count or 0))
        self.max_attempts = max(1, min(int(self.max_attempts or 3), 10))

        if self.status not in _VALID_STATUSES:
            frappe.throw("Invalid media processing status")
        if self.operation not in _VALID_OPERATIONS:
            frappe.throw("Invalid media processing operation")
        if not self.owner_user or not frappe.db.exists("User", self.owner_user):
            frappe.throw("Invalid media processing owner")
        if not self.source_media or not frappe.db.exists("AOS Media Object", self.source_media):
            frappe.throw("Invalid source media")
        policy = get_media_purpose(self.result_purpose)
        if not policy or "image/png" not in policy.allowed_content_types:
            frappe.throw("Invalid background-removal result purpose")
        if not _HEX_64.fullmatch(self.request_key):
            frappe.throw("Invalid media processing request key")
        if self.attempt_count > self.max_attempts:
            frappe.throw("Media processing attempt count is invalid")

        source_owner = frappe.db.get_value("AOS Media Object", self.source_media, "owner_user")
        if source_owner != self.owner_user:
            frappe.throw("Media processing source owner does not match job owner")
        if self.result_media:
            result = frappe.db.get_value(
                "AOS Media Object",
                self.result_media,
                ["owner_user", "processing_job"],
                as_dict=True,
            )
            if not result or result.owner_user != self.owner_user:
                frappe.throw("Media processing result owner does not match job owner")
            if self.name and str(result.processing_job or "") != self.name:
                frappe.throw("Media processing result is not linked to this job")
        if self.status == "Succeeded" and not self.result_media:
            frappe.throw("Successful media processing requires result media")
