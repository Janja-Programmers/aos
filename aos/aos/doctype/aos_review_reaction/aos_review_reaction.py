# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSReviewReaction(Document):
    def before_insert(self):
        if frappe.session.user == "Guest":
            frappe.throw("Login required")

        self.user = frappe.session.user

    def validate(self):
        review = frappe.get_doc("AOS Review", self.review)

        if review.reviewer == frappe.session.user:
            frappe.throw("You cannot react to your own review.")

        if review.status != "Approved":
            frappe.throw("You can only react to approved reviews.")

    def after_insert(self):
        update_review_reaction_counts(self.review)

    def on_update(self):
        update_review_reaction_counts(self.review)

    def on_trash(self):
        update_review_reaction_counts(self.review)


def update_review_reaction_counts(review_name):
    likes = frappe.db.count(
        "AOS Review Reaction",
        {"review": review_name, "reaction": "Like"}
    )

    dislikes = frappe.db.count(
        "AOS Review Reaction",
        {"review": review_name, "reaction": "Dislike"}
    )

    frappe.db.set_value(
        "AOS Review",
        review_name,
        {
            "like_count": likes,
            "dislike_count": dislikes
        },
        update_modified=False
    )
