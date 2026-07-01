# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from aos.api.shorts.constants import (
    DEFAULT_SOUND_SOURCE_TYPE,
    DEFAULT_SOUND_STATUS,
    VALID_SOUND_SOURCE_TYPES,
    VALID_SOUND_STATUSES,
    MAX_SOUND_DURATION_SECONDS,
)


class AOSSound(Document):
    def before_insert(self):
        self._set_defaults()

    def validate(self):
        self._set_defaults()
        self._validate_title()
        self._validate_source_type()
        self._validate_status()
        self._validate_audio_reference()
        self._validate_duration()
        self._normalize_flags()

    def _set_defaults(self):
        if not self.status:
            self.status = DEFAULT_SOUND_STATUS

        if not self.source_type:
            self.source_type = DEFAULT_SOUND_SOURCE_TYPE

        if not self.owner:
            self.owner = frappe.session.user if frappe.session.user != "Guest" else None

        if self.usage_count in (None, ""):
            self.usage_count = 0

        if getattr(self, "favorite_count", None) in (None, ""):
            self.favorite_count = 0

    def _validate_title(self):
        if not self.title:
            frappe.throw("Sound title is required")

        self.title = str(self.title).strip()
        if not self.title:
            frappe.throw("Sound title is required")

    def _validate_source_type(self):
        self.source_type = str(self.source_type or DEFAULT_SOUND_SOURCE_TYPE).strip().lower()
        if self.source_type not in VALID_SOUND_SOURCE_TYPES:
            frappe.throw("Invalid sound source type")

    def _validate_status(self):
        self.status = str(self.status or DEFAULT_SOUND_STATUS).strip().lower()
        if self.status not in VALID_SOUND_STATUSES:
            frappe.throw("Invalid sound status")

    def _validate_audio_reference(self):
        if not self.sound_media and not self.file_key and not self.file_url:
            frappe.throw("Sound file is required")

    def _validate_duration(self):
        if self.duration_seconds in (None, ""):
            self.duration_seconds = 0
            return

        try:
            self.duration_seconds = float(self.duration_seconds or 0)
        except Exception:
            frappe.throw("Invalid sound duration")

        if self.duration_seconds < 0:
            frappe.throw("Invalid sound duration")

        if self.duration_seconds > MAX_SOUND_DURATION_SECONDS:
            frappe.throw(f"Sound duration cannot exceed {MAX_SOUND_DURATION_SECONDS} seconds")

    def _normalize_flags(self):
        self.is_commercial_safe = 1 if int(self.is_commercial_safe or 0) else 0
