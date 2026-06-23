# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now


class AOSUserReport(Document):
    def validate(self):
        self._validate_users()
        self._validate_reason()
        self._prevent_duplicate_active_reports()
        self._validate_admin_action()

    def before_save(self):
        self._stamp_review_metadata()

    def on_update(self):
        self._apply_admin_action()

    def _validate_users(self):
        if not self.reported_user:
            frappe.throw("Reported user is required.")

        if not self.reported_by:
            frappe.throw("Reported by is required.")

        if self.reported_user == self.reported_by:
            frappe.throw("You cannot report yourself.")

        if not frappe.db.exists("User", self.reported_user):
            frappe.throw("Reported user does not exist.")

        if not frappe.db.exists("User", self.reported_by):
            frappe.throw("Reporting user does not exist.")

    def _validate_reason(self):
        if not self.reason:
            frappe.throw("Reason is required.")

        reason = frappe.db.get_value(
            "AOS Report Reason",
            self.reason,
            ["name", "is_active"],
            as_dict=True,
        )

        if not reason:
            frappe.throw("Invalid report reason.")

        if not int(reason.is_active or 0):
            frappe.throw("Selected report reason is inactive.")

    def _prevent_duplicate_active_reports(self):
        if not self.reported_user or not self.reported_by:
            return

        exists = frappe.db.exists(
            "AOS User Report",
            {
                "reported_user": self.reported_user,
                "reported_by": self.reported_by,
                "status": ["!=", "Rejected"],
                "name": ["!=", self.name],
            },
        )

        if exists:
            frappe.throw("You have already reported this user.")

    def _validate_admin_action(self):
        if not self.admin_action:
            return

        if self.status != "Resolved":
            frappe.throw("Admin action can only be applied when status is Resolved.")

    def _apply_admin_action(self):
        if not self.admin_action or self.status != "Resolved":
            return

        previous = self.get_doc_before_save()

        # Only run moderation when admin action changes.
        if previous and previous.admin_action == self.admin_action:
            return

        if self.admin_action == "Suspend User":
            frappe.db.set_value(
                "User",
                self.reported_user,
                "enabled",
                0,
                update_modified=False,
            )

            if frappe.db.exists("AOS Profile", self.reported_user):
                updates = {}
                meta = frappe.get_meta("AOS Profile")

                if meta.has_field("account_status"):
                    updates["account_status"] = "Suspended"

                if meta.has_field("is_deleted"):
                    updates["is_deleted"] = 0

                if updates:
                    frappe.db.set_value(
                        "AOS Profile",
                        self.reported_user,
                        updates,
                        update_modified=False,
                    )

        elif self.admin_action == "Warn User":
            # Warning delivery can be implemented later through notifications/email.
            pass

        elif self.admin_action == "Dismiss Report":
            # Explicit no-op for moderation audit clarity.
            pass

    def _stamp_review_metadata(self):
        """Stamp moderation metadata before the report update is saved."""
        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        # Do not stamp on initial report creation.
        if not previous:
            return

        status_changed = previous.status != self.status
        admin_action_changed = previous.admin_action != self.admin_action

        if not status_changed and not admin_action_changed:
            return

        # Only review states/actions should stamp metadata.
        if self.status == "Reviewing" and not self.admin_action:
            return

        self.reviewed_by = frappe.session.user
        self.reviewed_on = now()
