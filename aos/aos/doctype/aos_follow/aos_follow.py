# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from aos.services.account_service import get_or_create_profile


class AOSFollow(Document):
    def validate(self):
        self._validate_users()
        self._ensure_profiles()

    def after_insert(self):
        self._increment_follow_counts()

    def on_trash(self):
        self._decrement_follow_counts()

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

    def _ensure_profiles(self):
        get_or_create_profile(self.following_user)
        get_or_create_profile(self.follower_user)

    def _increment_follow_counts(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_followers = total_followers + 1
            WHERE name = %s
            """,
            (self.following_user,),
        )

        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_following = total_following + 1
            WHERE name = %s
            """,
            (self.follower_user,),
        )

    def _decrement_follow_counts(self):
        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_followers = GREATEST(total_followers - 1, 0)
            WHERE name = %s
            """,
            (self.following_user,),
        )

        frappe.db.sql(
            """
            UPDATE `tabAOS Profile`
            SET total_following = GREATEST(total_following - 1, 0)
            WHERE name = %s
            """,
            (self.follower_user,),
        )
