# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now


class AOSReview(Document):
    def before_insert(self):
        if frappe.session.user == "Guest":
            frappe.throw("Login required")

        self.reviewer = frappe.session.user
        self.status = "Pending"

    def validate(self):
        self.prevent_duplicate_review()

    def before_save(self):
        self._stamp_review_metadata()

    def on_update(self):
        update_ad_rating(self.ad)
        update_seller_rating_from_ad(self.ad)

    def on_trash(self):
        update_ad_rating(self.ad)
        update_seller_rating_from_ad(self.ad)

    def prevent_duplicate_review(self):
        """Ensure a user can only review an ad once."""
        if not self.ad or not self.reviewer:
            return

        exists = frappe.db.exists(
            "AOS Review",
            {
                "ad": self.ad,
                "reviewer": self.reviewer,
                "name": ["!=", self.name],
            },
        )

        if exists:
            frappe.throw("You have already reviewed this ad.")

    def _stamp_review_metadata(self):
        """Stamp moderation metadata when status changes."""
        if frappe.session.user == "Guest":
            return

        previous = self.get_doc_before_save()

        if not previous or previous.status != self.status:
            self.reviewed_by = frappe.session.user
            self.reviewed_on = now()


def update_ad_rating(ad_name):
    """Recalculate rating metrics for an Ad."""
    result = frappe.db.sql(
        """
        SELECT 
            AVG(rating) AS avg_rating,
            COUNT(name) AS total_reviews
        FROM `tabAOS Review`
        WHERE ad = %s
        AND status = 'Approved'
        """,
        (ad_name,),
        as_dict=True,
    )

    row = result[0] if result else {}

    frappe.db.set_value(
        "AOS Ad",
        ad_name,
        {
            "average_rating": round(row.get("avg_rating") or 0, 2),
            "total_reviews": row.get("total_reviews") or 0,
        },
        update_modified=False,
    )

def update_seller_rating_from_ad(ad_name):
    """Aggregate seller rating across all their ads."""

    seller = frappe.db.get_value("AOS Ad", ad_name, "seller")

    if not seller:
        return

    result = frappe.db.sql(
        """
        SELECT 
            AVG(r.rating) AS avg_rating,
            COUNT(r.name) AS total_reviews
        FROM `tabAOS Review` r
        INNER JOIN `tabAOS Ad` a ON r.ad = a.name
        WHERE a.seller = %s
        AND r.status = 'Approved'
        """,
        (seller,),
        as_dict=True,
    )

    row = result[0] if result else {}

    frappe.db.set_value(
        "AOS Seller",
        seller,
        {
            "rating": round(row.get("avg_rating") or 0, 2),
            "total_reviews": row.get("total_reviews") or 0,
        },
        update_modified=False,
    )
