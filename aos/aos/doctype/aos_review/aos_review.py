# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSReview(Document):
    def before_insert(self):
        if frappe.session.user == "Guest":
            frappe.throw("Login required")

        self.reviewer = frappe.session.user
        self.status = "Pending"

    def validate(self):
        ad = frappe.get_doc("AOS Ad", self.ad)

        # Prevent duplicate review
        existing = frappe.get_all(
            "AOS Review",
            filters={
                "ad": self.ad,
                "reviewer": frappe.session.user,
                "name": ["!=", self.name]
            },
            limit=1
        )

        if existing:
            frappe.throw("You have already reviewed this ad.")

    def on_update(self):
        # If status changed or rating updated
        update_ad_rating(self.ad)

    def on_trash(self):
        update_ad_rating(self.ad)


def update_ad_rating(ad_name):
    reviews = frappe.get_all(
        "AOS Review",
        filters={
            "ad": ad_name,
            "status": "Approved"
        },
        fields=["rating"]
    )

    if not reviews:
        frappe.db.set_value(
            "AOS Ad",
            ad_name,
            {
                "average_rating": 0,
                "total_reviews": 0
            },
            update_modified=False
        )
        return

    avg = sum(r.rating for r in reviews) / len(reviews)

    frappe.db.set_value(
        "AOS Ad",
        ad_name,
        {
            "average_rating": round(avg, 2),
            "total_reviews": len(reviews)
        },
        update_modified=False
    )
