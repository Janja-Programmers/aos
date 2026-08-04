# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import json
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.shorts.classification import normalize_scores

from aos.api.shorts.constants import (
    MAX_SHORT_DURATION_SECONDS,
    CAPTION_MAX_LENGTH,
    MAX_HASHTAGS,
    SHORT_CONTENT_MODE_SHOP,
    VALID_SHORT_CONTENT_MODES,
    DEFAULT_SHORT_AUDIENCE,
    VALID_SHORT_AUDIENCES,
    DEFAULT_ALLOW_COMMENTS,
    DEFAULT_ALLOW_DOWNLOADS,
)


class AOSShort(Document):
    def before_insert(self):
        self._set_defaults()

    def validate(self):
        self._validate_content_mode()
        self._validate_classification_metadata()
        self._validate_audience()
        self._validate_allow_comments()
        self._validate_allow_downloads()

        # Deleted shorts must not be blocked by old ad/content validation.
        if self.status == "deleted" or self.visibility_status == "deleted":
            return

        self._validate_ad()
        self._validate_content()
        self._validate_duration()
        self._handle_visibility_transition()

    # PRIVATE METHODS
    def _set_defaults(self):
        if not self.status:
            self.status = "initialized"

        if not self.visibility_status:
            self.visibility_status = "hidden"

        if not self.approval_status:
            self.approval_status = "auto_approved"

        if not self.audience:
            self.audience = DEFAULT_SHORT_AUDIENCE

        if self.allow_comments in (None, ""):
            self.allow_comments = DEFAULT_ALLOW_COMMENTS

        if getattr(self, "allow_downloads", None) in (None, ""):
            self.allow_downloads = DEFAULT_ALLOW_DOWNLOADS

        if getattr(self, "audio_mix_status", None) in (None, ""):
            self.audio_mix_status = "none"

    def _validate_content_mode(self):
        """
        Content mode is optional during upload/processing.

        It becomes required only when the short is being published
        through visibility_status = visible.
        """
        if not self.content_mode:
            return

        self.content_mode = self.content_mode.strip().lower()

        if self.content_mode not in VALID_SHORT_CONTENT_MODES:
            frappe.throw("Invalid short content mode")

    def _validate_classification_metadata(self):
        valid_statuses = {"pending", "visual_ready", "ready", "fallback", "failed", "legacy"}
        valid_sources = {
            "automatic_visual", "automatic_text", "automatic_visual_text",
            "commerce_context", "fallback", "legacy", "admin_override",
        }
        if not getattr(self, "classification_status", None):
            self.classification_status = "pending"
        if self.classification_status not in valid_statuses:
            frappe.throw("Invalid Shorts classification status")

        source = str(getattr(self, "classification_source", None) or "").strip()
        if source and source not in valid_sources:
            frappe.throw("Invalid Shorts classification source")

        try:
            confidence = float(getattr(self, "classification_confidence", 0) or 0)
        except (TypeError, ValueError):
            frappe.throw("Invalid Shorts classification confidence")
        if confidence < 0 or confidence > 1:
            frappe.throw("Invalid Shorts classification confidence")
        self.classification_confidence = confidence

        for fieldname in ("classification_visual_scores", "classification_scores"):
            value = getattr(self, fieldname, None)
            if value in (None, ""):
                continue
            if isinstance(value, str):
                try:
                    value = json.loads(value)
                except Exception:
                    frappe.throw("Invalid Shorts classification scores")
            if not isinstance(value, dict):
                frappe.throw("Invalid Shorts classification scores")
            normalized = normalize_scores(value)
            setattr(self, fieldname, json.dumps(normalized, separators=(",", ":"), sort_keys=True))

    def _validate_audience(self):
        """
        Validate short audience visibility.

        Supported values:
        - everyone: visible to everyone
        - followers: visible to users who follow the creator
        - friends: visible to mutual followers
        - only_me: visible only to the creator

        Audience defaults to everyone for backward compatibility.
        """
        if not self.audience:
            self.audience = DEFAULT_SHORT_AUDIENCE
            return

        self.audience = str(self.audience).strip().lower()

        if self.audience not in VALID_SHORT_AUDIENCES:
            frappe.throw("Invalid short audience")

    def _validate_allow_comments(self):
        """
        Normalize comment permission.

        allow_comments is a Check field:
        - 1 means comments are allowed
        - 0 means comments are disabled

        Missing values default to 1 for backward compatibility.
        """
        if self.allow_comments in (None, ""):
            self.allow_comments = DEFAULT_ALLOW_COMMENTS
            return

        try:
            self.allow_comments = 1 if int(self.allow_comments) else 0
        except Exception:
            frappe.throw("Invalid allow_comments value")

    def _validate_allow_downloads(self):
        """Normalize download permission.

        allow_downloads is intentionally independent of visibility/audience.
        A viewer must still pass privacy checks before a download URL is issued.
        """
        if getattr(self, "allow_downloads", None) in (None, ""):
            self.allow_downloads = DEFAULT_ALLOW_DOWNLOADS
            return

        try:
            self.allow_downloads = 1 if int(self.allow_downloads) else 0
        except Exception:
            frappe.throw("Invalid allow_downloads value")

    def _validate_ad(self):
        """
        Ad validation.

        Rules:
        - Draft/processing shorts may have no ad.
        - Shop shorts require an ad only when publishing.
        - If an ad is provided, it must exist and be active.
        - If seller is set, ad.seller must match seller.
        """
        if not self.ad:
            return

        ad = frappe.get_doc("AOS Ad", self.ad)

        if ad.status != "Active":
            frappe.throw("Shorts can only be attached to active ads")

        if self.seller and getattr(ad, "seller", None) != self.seller:
            frappe.throw("Invalid seller for this ad")

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
        Publishing rules.

        Upload lifecycle:
        - initialized/uploaded/processing/ready hidden records may be incomplete.
        - strict publishing rules apply only when visibility_status becomes visible.

        Publishing rules:
        - Short must be ready before becoming visible.
        - Content mode is required before becoming visible.
        - Audience must be valid before becoming visible.
        - Comment permission is normalized before publishing.
        - Download permission is normalized before publishing.
        - Shop shorts require seller + active ad.
        - Non-shop shorts must not be attached to an ad.
        - posted_on is set once when first published.
        """
        previous = self.get_doc_before_save()

        is_becoming_visible = False
        is_becoming_hidden = False

        if self.is_new():
            is_becoming_visible = self.visibility_status == "visible"
        elif previous:
            is_becoming_visible = (
                previous.visibility_status != "visible"
                and self.visibility_status == "visible"
            )
            is_becoming_hidden = (
                previous.visibility_status == "visible"
                and self.visibility_status == "hidden"
            )

        if not is_becoming_visible:
            if is_becoming_hidden:
                self.hidden_reason = self.hidden_reason or "Manually hidden"
            return

        if self.status != "ready":
            frappe.throw("Short must be ready before publishing")

        if not self.content_mode:
            frappe.throw("Content mode is required before publishing")

        if not self.audience:
            self.audience = DEFAULT_SHORT_AUDIENCE

        if self.audience not in VALID_SHORT_AUDIENCES:
            frappe.throw("Invalid short audience")

        if self.allow_comments in (None, ""):
            self.allow_comments = DEFAULT_ALLOW_COMMENTS

        self.allow_comments = 1 if int(self.allow_comments or 0) else 0

        if getattr(self, "allow_downloads", None) in (None, ""):
            self.allow_downloads = DEFAULT_ALLOW_DOWNLOADS

        self.allow_downloads = 1 if int(self.allow_downloads or 0) else 0

        if self.content_mode == SHORT_CONTENT_MODE_SHOP:
            if not self.seller:
                frappe.throw("Shop shorts must be linked to a seller before publishing")

            if not self.ad:
                frappe.throw("Shop shorts must be attached to an ad before publishing")
        else:
            if self.ad:
                frappe.throw("Only shop shorts can be attached to an ad")

        if not self.posted_on:
            self.posted_on = now_datetime()

        if self.ad:
            ad = frappe.get_doc("AOS Ad", self.ad)
            if ad.status != "Active":
                frappe.throw("Shorts can only be attached to active ads")
