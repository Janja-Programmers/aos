# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.live_analytics_service import LiveAnalyticsService


ACTIVE_COMMENT_STATUS = "active"
LIVE_STATUS = "live"


class AOSLiveStreamComment(Document):
    def validate(self):
        self._validate_required_fields()
        self._validate_user()
        self._validate_live_is_active()
        self._validate_parent()
        self._normalize_status()

    def before_insert(self):
        self._set_root_comment()

    def after_insert(self):
        self._increment_reply_count()
        self._update_live_comment_count()

    def on_trash(self):
        self._decrement_reply_count()
        self._decrement_live_comment_count()

    # VALIDATIONS
    def _validate_required_fields(self):
        if not self.live_stream:
            frappe.throw("Live stream is required.")

        if not self.user:
            frappe.throw("User is required.")

        if not self.content or not str(self.content).strip():
            frappe.throw("Comment content is required.")

        self.content = str(self.content).strip()

    def _validate_user(self):
        user = frappe.db.get_value(
            "User",
            self.user,
            ["name", "enabled"],
            as_dict=True,
        )

        if not user:
            frappe.throw("Invalid user.")

        if not user.enabled:
            frappe.throw("User account is disabled.")

    def _validate_live_is_active(self):
        live = frappe.db.get_value(
            "AOS Live Stream",
            self.live_stream,
            ["name", "status", "is_active"],
            as_dict=True,
        )

        if not live:
            frappe.throw("Invalid live stream.")

        if live.status != LIVE_STATUS or not live.is_active:
            frappe.throw("Cannot comment on inactive live stream.")

    def _validate_parent(self):
        if not self.parent_comment:
            return

        parent = frappe.db.get_value(
            "AOS Live Stream Comment",
            self.parent_comment,
            ["name", "live_stream", "status", "root_comment"],
            as_dict=True,
        )

        if not parent:
            frappe.throw("Invalid parent comment.")

        if parent.live_stream != self.live_stream:
            frappe.throw("Parent comment must belong to the same live stream.")

        if parent.status != ACTIVE_COMMENT_STATUS:
            frappe.throw("Cannot reply to an inactive comment.")

    def _normalize_status(self):
        if not self.status:
            self.status = ACTIVE_COMMENT_STATUS

    # SETTERS
    def _set_root_comment(self):
        if not self.parent_comment:
            return

        parent = frappe.db.get_value(
            "AOS Live Stream Comment",
            self.parent_comment,
            ["name", "root_comment"],
            as_dict=True,
        )

        if not parent:
            frappe.throw("Invalid parent comment.")

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

    def _decrement_reply_count(self):
        if not self.parent_comment:
            return

        frappe.db.sql(
            """
            UPDATE `tabAOS Live Stream Comment`
            SET reply_count = GREATEST(COALESCE(reply_count, 0) - 1, 0)
            WHERE name = %s
            """,
            (self.parent_comment,),
        )

    def _update_live_comment_count(self):
        if self.status != ACTIVE_COMMENT_STATUS:
            return

        LiveAnalyticsService.handle_comment_added(
            live_id=self.live_stream,
        )

    def _decrement_live_comment_count(self):
        if self.status != ACTIVE_COMMENT_STATUS:
            return

        LiveAnalyticsService.handle_comment_deleted(
            live_id=self.live_stream,
        )
