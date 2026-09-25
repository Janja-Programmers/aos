# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.activity.constants import (
    ACTIVE_STATUS,
    ACTIVITY_COUNT_MAX,
    CLEARED_STATUS,
    EVENT_MODE_COALESCE,
    EVENT_MODE_ONCE,
    EVENT_SPECS,
    HIDDEN_STATUS,
    VALID_ACTIVITY_STATUSES,
)
from aos.services.activity.identity import new_activity_id, normalize_activity_id
from aos.services.activity_service import ActivityService


class AOSUserActivity(Document):
    """Server-owned private Activity Center read-model row."""

    def before_insert(self):
        # Public identity is always server-generated; producer input cannot pick
        # or reuse another Activity row's public identifier.
        self.public_id = new_activity_id()

    def validate(self):
        self._validate_user()
        self._validate_public_id()
        self._validate_taxonomy()
        self._validate_status()
        self._set_defaults()
        self._normalize_count()
        self._normalize_metadata()
        self._protect_integrity_keys()
        self._validate_integrity_keys()
        self._protect_identity_fields()

    def _validate_user(self):
        if not self.user or self.user == "Guest" or not frappe.db.exists("User", self.user):
            frappe.throw("Invalid Activity owner")

    def _validate_public_id(self):
        if not normalize_activity_id(self.public_id):
            frappe.throw("Invalid Activity public identifier")

    def _validate_taxonomy(self):
        spec = EVENT_SPECS.get(str(self.activity_type or "").strip())
        if not spec:
            frappe.throw("Invalid Activity type")
        if self.activity_group != spec["group"]:
            frappe.throw("Invalid Activity group")
        if str(self.route_type or "") != spec["route_type"]:
            frappe.throw("Invalid Activity route")
        if str(self.target_doctype or "") != spec["target_doctype"]:
            frappe.throw("Invalid Activity target")

        if not str(self.route_id or "").strip():
            frappe.throw("Activity public resource identity is required")
        if str(spec["target_doctype"]) and not str(self.target_name or "").strip():
            frappe.throw("Activity internal resource identity is required")

    def _validate_status(self):
        if not self.status:
            self.status = ACTIVE_STATUS
        if self.status not in VALID_ACTIVITY_STATUSES:
            frappe.throw("Invalid Activity status")
        if not self.is_new():
            previous = frappe.db.get_value(self.doctype, self.name, "status")
            if previous in {HIDDEN_STATUS, CLEARED_STATUS} and self.status != previous:
                frappe.throw("Completed Activity history cannot be reactivated")

    def _set_defaults(self):
        now = now_datetime()
        if not self.occurred_at:
            self.occurred_at = now
        if not self.last_occurrence_at:
            self.last_occurrence_at = self.occurred_at or now

    def _normalize_count(self):
        try:
            self.count = min(max(int(self.count or 0), 1), ACTIVITY_COUNT_MAX)
        except (TypeError, ValueError):
            self.count = 1

    def _normalize_metadata(self):
        raw = self.metadata_json
        if isinstance(raw, str):
            try:
                raw = frappe.parse_json(raw) if raw.strip() else {}
            except Exception:
                frappe.throw("Invalid Activity metadata")
        if raw is None:
            raw = {}
        try:
            self.metadata_json = ActivityService._bounded_metadata(
                activity_type=str(self.activity_type),
                metadata=raw,
            )
        except (TypeError, ValueError):
            frappe.throw("Invalid Activity metadata")

    def _protect_integrity_keys(self):
        unique_key = ActivityService.normalize_unique_key(self.unique_key)
        if not unique_key:
            frappe.throw("Activity dedupe identity is required")
        # Enforce the same one-way hidden identity even if an internal caller
        # bypasses ActivityService and constructs the DocType directly.
        self.unique_key = unique_key
        spec = EVENT_SPECS[str(self.activity_type)]
        if self.status == ACTIVE_STATUS and spec["mode"] == EVENT_MODE_COALESCE and unique_key:
            self.active_key = ActivityService.active_key_for(user=self.user, unique_key=unique_key)
        else:
            self.active_key = None
        if spec["mode"] == EVENT_MODE_ONCE and unique_key:
            self.event_key = ActivityService.event_key_for(user=self.user, unique_key=unique_key)
        else:
            self.event_key = None

    def _validate_integrity_keys(self):
        spec = EVENT_SPECS[str(self.activity_type)]
        mode = str(spec["mode"])
        if mode == EVENT_MODE_COALESCE and self.status == ACTIVE_STATUS and not self.active_key:
            frappe.throw("Repeatable Activity requires an active integrity key")
        if mode == EVENT_MODE_ONCE and not self.event_key:
            frappe.throw("One-off Activity requires an event integrity key")

    def _protect_identity_fields(self):
        if self.is_new():
            return
        identity_fields = [
            "user", "public_id", "activity_group", "activity_type",
            "target_doctype", "target_name", "route_type", "route_id",
            "unique_key", "event_key",
        ]
        previous = frappe.db.get_value(
            self.doctype,
            self.name,
            identity_fields,
            as_dict=True,
        )
        if not previous:
            return
        immutable = {fieldname: getattr(self, fieldname, None) for fieldname in identity_fields}
        for fieldname, current in immutable.items():
            if str(previous.get(fieldname) or "") != str(current or ""):
                frappe.throw(f"Activity {fieldname} cannot be changed")
