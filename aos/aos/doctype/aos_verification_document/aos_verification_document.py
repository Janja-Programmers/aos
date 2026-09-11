# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.verification.constants import MAX_DOCUMENT_NUMBER_LENGTH, MAX_DOCUMENT_TYPE_LENGTH


class AOSVerificationDocument(Document):
    """Sensitive child row; parent Verification owns authorization and Media policy."""

    def validate(self):
        self.document_type = self._clean(self.document_type, MAX_DOCUMENT_TYPE_LENGTH)
        self.document_number = self._clean(self.document_number, MAX_DOCUMENT_NUMBER_LENGTH)
        if not self.document_type:
            frappe.throw("Verification document type is required")
        if not str(self.media or "").strip():
            frappe.throw("Verification document media is required")
        if self.issue_date and self.expiry_date and self.issue_date > self.expiry_date:
            frappe.throw("Document issue date cannot be after expiry date")

    @staticmethod
    def _clean(value, limit: int) -> str:
        text = " ".join(str(value or "").replace("\x00", "").split())
        if len(text) > limit:
            frappe.throw("Verification document field is too long")
        return text
