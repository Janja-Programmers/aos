# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from aos.services.social.repository import SocialRepository


class AOSFollow(Document):
    def validate(self):
        self._validate_users()
        repository = SocialRepository()
        repository.lock_account_pair(user_a=self.follower_user, user_b=self.following_user)
        self._validate_active_accounts(repository)
        self._validate_not_blocked(repository)

    def after_insert(self):
        self._increment_follow_counts()

    def on_trash(self):
        self._decrement_follow_counts()

    # VALIDATION
    def _validate_users(self):
        if not self.following_user:
            frappe.throw("Following user is required.")

        if not self.follower_user:
            frappe.throw("Follower user is required.")

        if self.following_user == self.follower_user:
            frappe.throw("You cannot follow yourself.")

        if not frappe.db.exists("User", self.following_user):
            frappe.throw("User to follow does not exist.")

        if not frappe.db.exists("User", self.follower_user):
            frappe.throw("Follower user does not exist.")

        if not frappe.db.exists("AOS Profile", {"user": self.following_user}):
            frappe.throw("Following user profile does not exist.")

        if not frappe.db.exists("AOS Profile", {"user": self.follower_user}):
            frappe.throw("Follower user profile does not exist.")


    def _validate_active_accounts(self, repository: SocialRepository):
        for user, label in (
            (self.follower_user, "Follower user"),
            (self.following_user, "Following user"),
        ):
            state = repository.account_state(user)
            if not state or int(state.get("enabled") or 0) != 1:
                frappe.throw(f"{label} is unavailable.")
            if str(state.get("account_status") or "Active") != "Active" or int(state.get("is_deleted") or 0):
                frappe.throw(f"{label} is unavailable.")

    def _validate_not_blocked(self, repository: SocialRepository):
        _outgoing, _incoming, blocks = repository.relationship_sets(
            viewer=self.follower_user,
            targets=[self.following_user],
        )
        blocked_by_me, blocked_me = blocks.get(self.following_user, (False, False))
        if blocked_by_me or blocked_me:
            frappe.throw("Follow relationship is unavailable.")

    # COUNTS
    def _increment_follow_counts(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_followers = total_followers + 1
            WHERE user = %s
            """,
            (self.following_user,),
        )

        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_following = total_following + 1
            WHERE user = %s
            """,
            (self.follower_user,),
        )

    def _decrement_follow_counts(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_followers = GREATEST(total_followers - 1, 0)
            WHERE user = %s
            """,
            (self.following_user,),
        )

        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_following = GREATEST(total_following - 1, 0)
            WHERE user = %s
            """,
            (self.follower_user,),
        )
