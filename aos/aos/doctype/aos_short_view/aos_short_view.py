# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe

from aos.services.shorts.analytics import bounded_watch_ms
from aos.services.shorts.policy import can_view
from frappe.model.document import Document
from frappe.utils import now_datetime, getdate

from aos.api.shorts.constants import (
    MIN_VIEW_MS,
    MIN_VIEW_PERCENT,
)


class AOSShortView(Document):
    def validate(self):
        self._set_defaults()
        self._validate_identity()
        self._sync_identity_key()
        self._validate_short()

    def before_insert(self):
        self._set_date()

    def on_update(self):
        self._handle_qualification()

    def _set_defaults(self):
        if not self.view_date:
            self.view_date = getdate()

        if self.watch_ms is None:
            self.watch_ms = 0

    def _set_date(self):
        if not self.view_date:
            self.view_date = getdate()

    def _validate_identity(self):
        if not self.user and not self.session_id:
            frappe.throw("User or session_id is required")

    def _sync_identity_key(self):
        if self.user:
            self.identity_key = f"user:{self.user}"
            return

        if self.session_id:
            self.identity_key = f"session:{self.session_id}"
            return

        self.identity_key = None

    def _validate_short(self):
        if not self.short:
            frappe.throw("Short is required")

        short = frappe.db.get_value(
            "AOS Short",
            self.short,
            ["name", "owner", "status", "visibility_status", "approval_status", "audience", "duration_seconds"],
            as_dict=True,
        )

        if not short:
            frappe.throw("Short not found")

        if short.status != "ready":
            frappe.throw("Short is not available")

        if short.visibility_status != "visible":
            frappe.throw("Short is not visible")
        viewer = self.user if self.user and self.user != "Guest" else None
        if not can_view(short, viewer=viewer):
            frappe.throw("Short is not available")

        self._short_duration = short.duration_seconds or 0
        self.watch_ms = bounded_watch_ms(self.watch_ms, duration_seconds=self._short_duration)

    def _handle_qualification(self):
        """Check if view qualifies and update count"""
        if self.qualified:
            return

        if not self.watch_ms:
            return

        qualifies = False

        # Rule 1: minimum watch time
        if self.watch_ms >= MIN_VIEW_MS:
            qualifies = True

        # Rule 2: percentage watched
        elif self._short_duration:
            if self.watch_ms >= (self._short_duration * 1000 * MIN_VIEW_PERCENT):
                qualifies = True

        if not qualifies:
            return

        # Mark as qualified
        frappe.db.set_value(
            self.doctype,
            self.name,
            "qualified",
            1,
            update_modified=False,
        )

        # Increment view count safely
        frappe.db.sql(
            """
            UPDATE `tabAOS Short`
            SET view_count = view_count + 1
            WHERE name = %s
            """,
            (self.short,),
        )

        # Update last engagement
        frappe.db.set_value(
            "AOS Short",
            self.short,
            "last_engagement_at",
            now_datetime(),
            update_modified=False,
        )
