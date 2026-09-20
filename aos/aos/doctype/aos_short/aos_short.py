# Copyright (c) 2026, Africa Online Stores and contributors
from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.shorts.identity import generate_short_id

_CONTENT_TYPES = {"Video", "Photo"}
_LIFECYCLE = {"Draft", "Processing", "Pending Review", "Published", "Rejected", "Hidden", "Failed", "Deleted"}
_PROCESSING = {"Not Required", "Queued", "Processing", "Retry Waiting", "Ready", "Failed", "Cancelled"}
_MODERATION = {"Draft", "Pending", "Approved", "Rejected", "Hidden"}
_AUDIENCES = {"everyone", "followers", "friends", "only_me"}

class AOSShort(Document):
    def autoname(self):
        self.name = generate_short_id()

    def before_insert(self):
        self.revision = int(self.revision or 1)
        self.processing_generation = int(self.processing_generation or 0)
        self.moderation_generation = int(self.moderation_generation or 0)
        self.lifecycle_status = self.lifecycle_status or "Draft"
        self.moderation_status = self.moderation_status or "Draft"
        self.audience = self.audience or "everyone"
        if self.content_type == "Video":
            self.processing_status = self.processing_status or "Queued"
        else:
            self.processing_status = "Not Required"

    def validate(self):
        if self.content_type not in _CONTENT_TYPES: frappe.throw("Invalid Short content type")
        if self.lifecycle_status not in _LIFECYCLE: frappe.throw("Invalid Short lifecycle status")
        if self.processing_status not in _PROCESSING: frappe.throw("Invalid Short processing status")
        if self.moderation_status not in _MODERATION: frappe.throw("Invalid Short moderation status")
        if self.audience not in _AUDIENCES: frappe.throw("Invalid Short audience")
        if len(str(self.caption or "")) > 1000: frappe.throw("Short caption is too long")
        if self.content_type == "Video" and self.lifecycle_status not in {"Draft", "Deleted"} and not self.raw_video_media:
            frappe.throw("Video Short requires raw video Media")
        if self.content_type == "Photo" and self.raw_video_media:
            frappe.throw("Photo Short cannot reference raw video Media")
        if self.lifecycle_status == "Published":
            if self.processing_status not in {"Ready", "Not Required"}: frappe.throw("Short is not technically ready")
            if self.moderation_status != "Approved": frappe.throw("Short is not approved")
            if not self.posted_on: self.posted_on = now_datetime()
        if self.lifecycle_status == "Deleted" and not self.deleted_at:
            self.deleted_at = now_datetime()
