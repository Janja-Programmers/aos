# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


VALID_STATUSES = {
    "Initialized",
    "Uploaded",
    "Attached",
    "Orphaned",
    "Delete Pending",
    "Deleted",
}

VALID_VISIBILITIES = {
    "Public",
    "Private",
}


class AOSMediaObject(Document):
    """Metadata record for MinIO-backed AOS media.

    The bytes live in MinIO. This DocType stores only ownership, object location,
    visibility, purpose, lifecycle status, and optional attachment metadata.
    """

    def validate(self):
        self._normalize_values()
        self._validate_required_fields()
        self._validate_enums()
        self._validate_object_key()
        self._validate_size()
        self._validate_attachment_state()

    def _normalize_values(self):
        self.bucket = str(self.bucket or "").strip().strip("/")
        self.object_key = str(self.object_key or "").strip().strip("/")
        self.original_filename = str(self.original_filename or "").strip()
        self.content_type = str(self.content_type or "").strip().lower()
        self.etag = str(self.etag or "").strip()
        self.checksum = str(self.checksum or "").strip()
        self.purpose = str(self.purpose or "").strip()
        self.visibility = str(self.visibility or "").strip() or "Private"
        self.status = str(self.status or "").strip() or "Initialized"
        self.attached_doctype = str(self.attached_doctype or "").strip()
        self.attached_name = str(self.attached_name or "").strip()
        self.attached_field = str(self.attached_field or "").strip()
        self.public_url = str(self.public_url or "").strip()

    def _validate_required_fields(self):
        if not self.owner_user:
            frappe.throw("Owner user is required")
        if not frappe.db.exists("User", self.owner_user):
            frappe.throw("Invalid owner user")
        if not self.bucket:
            frappe.throw("Bucket is required")
        if not self.object_key:
            frappe.throw("Object key is required")
        if not self.purpose:
            frappe.throw("Purpose is required")

    def _validate_enums(self):
        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid media status")
        if self.visibility not in VALID_VISIBILITIES:
            frappe.throw("Invalid media visibility")

    def _validate_object_key(self):
        object_key = self.object_key or ""

        if object_key.startswith("/") or object_key.endswith("/"):
            frappe.throw("Object key must not start or end with a slash")

        parts = object_key.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            frappe.throw("Invalid object key")

        if "\\" in object_key:
            frappe.throw("Object key cannot contain backslashes")

    def _validate_size(self):
        try:
            size = int(self.size_bytes or 0)
        except Exception:
            size = 0

        if size < 0:
            frappe.throw("Size cannot be negative")

        self.size_bytes = size

    def _validate_attachment_state(self):
        if self.status == "Attached":
            if not self.attached_doctype or not self.attached_name:
                frappe.throw("Attached media requires attached doctype and name")
