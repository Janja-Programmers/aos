# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class AOSVerificationDocument(Document):
    def validate(self):
        self._validate_media_reference()

    def _validate_media_reference(self):
        # New rows should use private MinIO-backed AOS Media Object records.
        # attachment remains only as a legacy/cached URL field so old staging rows
        # do not break while features are migrated.
        if not self.media and not self.attachment:
            frappe.throw("Verification document media is required")

        if not self.media:
            return

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
