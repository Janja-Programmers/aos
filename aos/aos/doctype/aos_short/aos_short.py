# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.api.shorts.constants import (
    MAX_SHORT_DURATION_SECONDS,
    CAPTION_MAX_LENGTH,
    MAX_HASHTAGS,
)


class AOSShort(Document):
    def validate(self):
        self._validate_ad()
        self._validate_content()
        self._validate_duration()

    def before_insert(self):
        self._set_defaults()

    def on_update(self):
        self._handle_ready_transition()


    # PRIVATE METHODS
    def _validate_ad(self):
        if not self.ad:
            return

        ad = frappe.get_doc("AOS Ad", self.ad)

        if ad.status != "Active":
            frappe.throw("Shorts can only be created for active ads")

        # Ensure ownership
        if hasattr(ad, "seller") and ad.seller != self.seller:
            # seller may not yet be set at this stage
            if self.is_new():
                return
            frappe.throw("Invalid seller for this ad")
    def _set_defaults(self):
        if not self.status:
            self.status = "initialized"

        if not self.visibility_status:
            self.visibility_status = "visible"

        if not self.approval_status:
            self.approval_status = "auto_approved"

    def _validate_content(self):
        if self.caption:
            self.caption = self.caption.strip()

            if len(self.caption) > CAPTION_MAX_LENGTH:
                frappe.throw(f"Caption cannot exceed {CAPTION_MAX_LENGTH} characters")

        if self.hashtags:
            # Convert if string
            if isinstance(self.hashtags, str):
                try:
                    self.hashtags = json.loads(self.hashtags)
                except Exception:
                    frappe.throw("Invalid hashtags format")

            if not isinstance(self.hashtags, list):
                frappe.throw("Hashtags must be a list")

            cleaned = []
            for tag in self.hashtags:
                if not tag:
                    continue
                tag = str(tag).strip().lower().replace("#", "")
                if tag:
                    cleaned.append(tag)

            cleaned = list(dict.fromkeys(cleaned))

            if len(cleaned) > MAX_HASHTAGS:
                frappe.throw(f"Maximum {MAX_HASHTAGS} hashtags allowed")

            self.hashtags = json.dumps(cleaned)

    def _validate_duration(self):
        if self.duration_seconds:
            if self.duration_seconds > MAX_SHORT_DURATION_SECONDS:
                frappe.throw(
                    f"Short duration cannot exceed {MAX_SHORT_DURATION_SECONDS} seconds"
                )

    def _handle_ready_transition(self):
        """Handle logic when processing completes"""
        if self.status != "ready":
            return

        # Set posted_on once
        if not self.posted_on:
            self.posted_on = now_datetime()

        # Ensure visibility rules
        if self.visibility_status != "visible":
            return

        # Ensure ad is still active
        try:
            ad = frappe.get_doc("AOS Ad", self.ad)
            if ad.status != "Active":
                self.visibility_status = "hidden"
                self.hidden_reason = "Ad is no longer active"
        except Exception:
            # fail-safe
            self.visibility_status = "hidden"
            self.hidden_reason = "Ad not found"
