# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.api.shared.account_status import is_account_deleted
from aos.services.social.repository import SocialRepository


ACTIVE_STATUS = "Active"
UNBLOCKED_STATUS = "Unblocked"
VALID_STATUSES = {ACTIVE_STATUS, UNBLOCKED_STATUS}


class AOSUserBlock(Document):
    def validate(self):
        self._set_defaults()
        self._validate_status()
        self._validate_users()
        SocialRepository().lock_account_pair(user_a=self.blocker_user, user_b=self.blocked_user)
        self._sync_active_pair_key()

    def after_insert(self):
        self._enforce_active_block_side_effects()

    def on_update(self):
        self._enforce_active_block_side_effects()

    def _set_defaults(self):
        if not self.status:
            self.status = ACTIVE_STATUS

        if self.status == ACTIVE_STATUS:
            if not self.blocked_at:
                self.blocked_at = now_datetime()
            self.unblocked_at = None

        if self.status == UNBLOCKED_STATUS and not self.unblocked_at:
            self.unblocked_at = now_datetime()

    def _validate_status(self):
        if self.status not in VALID_STATUSES:
            frappe.throw("Invalid block status.")

    def _validate_users(self):
        if not self.blocker_user:
            frappe.throw("Blocker user is required.")

        if not self.blocked_user:
            frappe.throw("Blocked user is required.")

        if self.blocker_user == self.blocked_user:
            frappe.throw("You cannot block yourself.")

        self._validate_user(self.blocker_user, label="Blocker user")
        self._validate_user(self.blocked_user, label="Blocked user")

    def _validate_user(self, user: str, *, label: str):
        if not frappe.db.exists("User", user):
            frappe.throw(f"{label} does not exist.")

        if not frappe.db.exists("AOS Profile", {"user": user}):
            frappe.throw(f"{label} profile does not exist.")

        enabled = frappe.db.get_value("User", user, "enabled")
        if int(enabled or 0) != 1:
            frappe.throw(f"{label} is disabled.")

        if is_account_deleted(user):
            frappe.throw(f"{label} has been deleted.")

        if self.status == ACTIVE_STATUS:
            account_status = frappe.db.get_value("AOS Profile", {"user": user}, "account_status") or "Active"
            if account_status != "Active":
                frappe.throw(f"{label} is unavailable.")

    def _sync_active_pair_key(self):
        """Populate DB-enforced active-only uniqueness key.

        MariaDB unique indexes allow multiple NULL values, so inactive
        historical rows keep a NULL key while the active row owns the pair.
        """
        if self.status == ACTIVE_STATUS and self.blocker_user and self.blocked_user:
            self.active_pair_key = f"{self.blocker_user}|{self.blocked_user}"
            return

        self.active_pair_key = None

    def _enforce_active_block_side_effects(self):
        if self.status != ACTIVE_STATUS or not self.blocker_user or not self.blocked_user:
            return
        repository = SocialRepository()
        repository.remove_follows_both_directions(
            user_a=self.blocker_user,
            user_b=self.blocked_user,
        )
        repository.sync_counters({self.blocker_user, self.blocked_user})
