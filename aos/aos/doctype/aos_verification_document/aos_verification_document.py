# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.verification.constants import MAX_DOCUMENT_NUMBER_LENGTH, MAX_DOCUMENT_TYPE_LENGTH


class AOSVerificationDocument(Document):
    def validate(self):
        self.attachment = ""
        self.document_type = self._clean(self.document_type, MAX_DOCUMENT_TYPE_LENGTH)
        self.document_number = self._clean(self.document_number, MAX_DOCUMENT_NUMBER_LENGTH)
        if not self.document_type:
            frappe.throw("Verification document type is required")
        if self.issue_date and self.expiry_date and self.issue_date > self.expiry_date:
            frappe.throw("Document issue date cannot be after expiry date")
        self._validate_media_reference()

    @staticmethod
    def _clean(value, limit: int) -> str:
        text = " ".join(str(value or "").replace("\x00", "").split())
        if len(text) > limit:
            frappe.throw("Verification document field is too long")
        return text

    def _validate_media_reference(self):
        if not self.media:
            frappe.throw("Verification document media is required")
        media = frappe.db.get_value(
            "AOS Media Object",
            self.media,
            ["purpose", "visibility", "status"],
            as_dict=True,
        )
        if not media:
            frappe.throw("Invalid verification document media")
        if media.purpose != "verification_document":
            frappe.throw("Verification document media has the wrong purpose")
        if media.visibility != "Private":
            frappe.throw("Verification document media must be private")
        if media.status not in {"Uploaded", "Attached"}:
            frappe.throw("Verification document media must be uploaded")
