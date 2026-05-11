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

VALID_CONTENT_MODES = {
    "shop",
    "geo",
    "talent",
    "learn",
}


class AOSShort(Document):
    def validate(self):
        self._validate_content_mode()
        self._validate_ad()
        self._validate_content()
        self._validate_duration()

    def before_insert(self):
        self._set_defaults()

    def on_update(self):
        self._handle_visibility_transition()


    # PRIVATE METHODS
    def _validate_content_mode(self):
        if not self.content_mode:
            return
        
        self.content_mode = self.content_mode.strip().lower()

        if self.content_mode not in VALID_CONTENT_MODES:
            frappe.throw("Invalid short content mode")

    def _validate_ad(self):
        """
        Ad rules:
        - Shop shorts are commerce shorts and require an ad before publishing.
        - Non-shop shorts may exist without an ad.
        - If an ad is provided for any mode, it must be valid and active.
        """
        if not self.ad:
            return

        ad = frappe.get_doc("AOS Ad", self.ad)

        if ad.status != "Active":
            frappe.throw("Shorts can only be created for active ads")

        # Ensure ownership
        if self.seller and hasattr(ad, "seller") and ad.seller != self.seller:
            if self.is_new():
                return
            frappe.throw("Invalid seller for this ad")


    def _set_defaults(self):
        if not self.status:
            self.status = "initialized"

        if not self.visibility_status:
            self.visibility_status = "hidden"

        if not self.approval_status:
            self.approval_status = "auto_approved"


    def _validate_content(self):
        if self.caption:
            self.caption = self.caption.strip()

            if len(self.caption) > CAPTION_MAX_LENGTH:
                frappe.throw(f"Caption cannot exceed {CAPTION_MAX_LENGTH} characters")

        if self.hashtags is not None:
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


    def _handle_visibility_transition(self):
        """
        Handles publishing logic based on visibility_status.

        Publishing rules:
        - Short must be ready before becoming visible.
        - Shop shorts must have an active ad.
        - Geo/Talent/Learn shorts do not require an ad.
        """
        previous = self.get_doc_before_save()
        if not previous:
            return

        # Detect visibility change
        if previous.visibility_status == self.visibility_status:
            return

        # CASE: becoming visible (publishing)
        if self.visibility_status == "visible":

            # Must be fully processed
            if self.status != "ready":
                frappe.throw("Short must be ready before publishing")

            # Shop shorts must have ad
            if self.content_mode == "shop" and not self.ad:
                frappe.throw("Shop shorts must be attached to an ad before publishing")

            # Set posted_on once
            if not self.posted_on:
                self.posted_on = now_datetime()

            # Ensure ad is still valid
            if self.ad:
                try:
                    ad = frappe.get_doc("AOS Ad", self.ad)
                    if ad.status != "Active":
                        self.visibility_status = "hidden"
                        self.hidden_reason = "Ad is no longer active"
                except Exception:
                    self.visibility_status = "hidden"
                    self.hidden_reason = "Ad not found"

        # CASE: becoming hidden
        if self.visibility_status == "hidden":
            self.hidden_reason = self.hidden_reason or "Manually hidden"
