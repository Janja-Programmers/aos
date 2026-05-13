# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSFollow(Document):
    def validate(self):
        self._validate_users()
        self._validate_unique_follow()

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

        if not frappe.db.exists("AOS Profile", self.following_user):
            frappe.throw("Following user profile does not exist.")

        if not frappe.db.exists("AOS Profile", self.follower_user):
            frappe.throw("Follower user profile does not exist.")

    def _validate_unique_follow(self):
        """
        Prevent duplicate follow rows for the same follower/following pair.

        The database unique constraint should also exist for race-condition safety:
        (follower_user, following_user)
        """

        existing = frappe.db.exists(
            "AOS Follow",
            {
                "follower_user": self.follower_user,
                "following_user": self.following_user,
            },
        )

        if existing and existing != self.name:
            frappe.throw("You are already following this user.")

    # COUNTS
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
