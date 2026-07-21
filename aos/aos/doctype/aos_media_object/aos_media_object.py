# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

from aos.services.media.content_validation import (
    normalize_content_type,
    normalize_filename,
    validate_filename_extension,
)
from aos.services.media.media_purposes import get_media_purpose

VALID_STATUSES = {
    "Initialized", "Uploaded", "Processing", "Ready", "Attached",
    "Failed", "Orphaned", "Replaced", "Delete Pending", "Deleted",
}
VALID_VISIBILITIES = {"Public", "Private"}
HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class AOSMediaObject(Document):
    """Canonical metadata and lifecycle record for AOS-owned media bytes."""

    def validate(self):
        self._normalize_values()
        self._validate_required_fields()
        self._validate_enums_and_policy()
        self._validate_storage_identity(self.bucket, self.object_key, required=True)
        self._validate_storage_identity(self.upload_bucket, self.upload_object_key, required=False)
        self._validate_file_metadata()
        self._validate_attachment_state()
        self._validate_relationships()

    def _normalize_values(self):
        for field in ("bucket", "object_key", "upload_bucket", "upload_object_key"):
            setattr(self, field, str(getattr(self, field, "") or "").strip().strip("/"))
        self.original_filename = str(self.original_filename or "").strip()
        self.content_type = str(self.content_type or "").split(";", 1)[0].strip().lower()
        for field in (
            "etag", "expected_checksum", "checksum", "purpose", "visibility", "status",
            "attached_doctype", "attached_name", "attached_field", "public_url",
            "idempotency_key_hash", "failure_code", "failure_reason", "last_storage_error",
            "processing_error", "derived_from_media", "replaced_by_media",
        ):
            setattr(self, field, str(getattr(self, field, "") or "").strip())
        self.visibility = self.visibility or "Private"
        self.status = self.status or "Initialized"

    def _validate_required_fields(self):
        if not self.owner_user or not frappe.db.exists("User", self.owner_user):
            frappe.throw("Invalid owner user")
        if not self.bucket:
            frappe.throw("Bucket is required")
        if not self.object_key:
            frappe.throw("Object key is required")
        if not self.purpose:
            frappe.throw("Purpose is required")

    def _validate_enums_and_policy(self):
        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid media status")
        if self.visibility not in VALID_VISIBILITIES:
            frappe.throw("Invalid media visibility")
        policy = get_media_purpose(self.purpose)
        if not policy:
            frappe.throw("Invalid media purpose")
        if self.visibility != policy.visibility:
            frappe.throw("Media visibility does not match purpose policy")
        if self.visibility == "Private" and self.public_url:
            frappe.throw("Private media cannot persist a public URL")

        content_type = normalize_content_type(self.content_type)
        if content_type and content_type not in policy.allowed_content_types:
            frappe.throw("Media content type does not match purpose policy")
        if self.original_filename:
            try:
                validate_filename_extension(
                    self.original_filename, policy.allowed_extensions
                )
            except ValueError as exc:
                frappe.throw(str(exc))
        maximum = int(policy.max_size_bytes or 0)
        if maximum and (
            int(self.expected_size_bytes or 0) > maximum
            or int(self.size_bytes or 0) > maximum
        ):
            frappe.throw("Media size exceeds purpose policy")
        duration = float(self.duration_seconds or 0)
        if duration < 0:
            frappe.throw("Media duration cannot be negative")
        if policy.max_duration_seconds and duration > policy.max_duration_seconds:
            frappe.throw("Media duration exceeds purpose policy")

    @staticmethod
    def _validate_storage_identity(bucket, object_key, *, required: bool):
        bucket = str(bucket or "")
        object_key = str(object_key or "")
        if not bucket and not object_key and not required:
            return
        if not bucket or not object_key:
            frappe.throw("Storage bucket and object key must be provided together")
        if any(char in bucket for char in ("/", "\\", "\x00")) or bucket in {".", ".."}:
            frappe.throw("Invalid storage bucket")
        if object_key.startswith("/") or object_key.endswith("/") or "\\" in object_key or "\x00" in object_key:
            frappe.throw("Invalid object key")
        if any(part in {"", ".", ".."} for part in object_key.split("/")):
            frappe.throw("Invalid object key")

    def _validate_file_metadata(self):
        if self.original_filename:
            try:
                self.original_filename = normalize_filename(self.original_filename)
            except ValueError as exc:
                frappe.throw(str(exc))
        for field in ("expected_size_bytes", "size_bytes", "retry_count"):
            try:
                value = int(getattr(self, field, 0) or 0)
            except (TypeError, ValueError):
                frappe.throw(f"Invalid {field}")
            if value < 0:
                frappe.throw(f"{field} cannot be negative")
            setattr(self, field, value)
        for field in ("expected_checksum", "checksum", "idempotency_key_hash"):
            value = str(getattr(self, field, "") or "").lower()
            if value and not HEX_64.fullmatch(value):
                frappe.throw(f"Invalid {field}")
            setattr(self, field, value)

    def _validate_attachment_state(self):
        has_type = bool(self.attached_doctype)
        has_name = bool(self.attached_name)
        if has_type != has_name:
            frappe.throw("Attached doctype and name must be provided together")
        if self.status == "Attached" and not (has_type and has_name):
            frappe.throw("Attached media requires an owning resource")
        if self.status in {"Initialized", "Failed", "Orphaned", "Replaced", "Deleted"} and (has_type or has_name):
            frappe.throw("Current media state cannot retain an attachment")

    def _validate_relationships(self):
        if self.derived_from_media and self.derived_from_media == self.name:
            frappe.throw("Media cannot derive from itself")
        if self.replaced_by_media and self.replaced_by_media == self.name:
            frappe.throw("Media cannot replace itself")
