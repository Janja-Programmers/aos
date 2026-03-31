# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.live_analytics_service import LiveAnalyticsService


class AOSLiveStreamComment(Document):
    def validate(self):
        self._validate_live_is_active()
        self._validate_parent()

    def before_insert(self):
        self._set_root_comment()

    def after_insert(self):
        self._increment_reply_count()
        self._update_live_comment_count()

    def on_trash(self):
        self._decrement_live_comment_count()

    # VALIDATIONS
    def _validate_live_is_active(self):
        live = frappe.db.get_value(
            "AOS Live Stream",
            self.live_stream,
            ["status"],
            as_dict=True,
        )

        if not live:
            frappe.throw("Invalid live stream")

        if live.status != "live":
            frappe.throw("Cannot comment on inactive live stream")

    def _validate_parent(self):
        if not self.parent_comment:
            return

        parent = frappe.db.get_value(
            "AOS Live Stream Comment",
            self.parent_comment,
            ["live_stream"],
            as_dict=True,
        )

        if not parent:
            frappe.throw("Invalid parent comment")

        if parent.live_stream != self.live_stream:
            frappe.throw("Parent comment must belong to same live stream")

    # SETTERS
    def _set_root_comment(self):
        if not self.parent_comment:
            self.root_comment = self.name
            return

        parent = frappe.get_doc("AOS Live Stream Comment", self.parent_comment)
        self.root_comment = parent.root_comment or parent.name

    # SIDE EFFECTS
    def _increment_reply_count(self):
        if not self.parent_comment:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream Comment`
            SET reply_count = COALESCE(reply_count, 0) + 1
            WHERE name = %s
            """,
            (self.parent_comment,),
        )

    def _update_live_comment_count(self):
        LiveAnalyticsService.handle_comment_added(
            live_id=self.live_stream
        )

    def _decrement_live_comment_count(self):
        LiveAnalyticsService.handle_comment_deleted(
            live_id=self.live_stream
        )
