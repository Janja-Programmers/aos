# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSLocation(Document):
    def validate(self):
        self.country = (self.country or "").strip()
        self.location = (self.location or "").strip()
        if not self.country or not frappe.db.exists("Country", self.country):
            frappe.throw("Invalid country.", frappe.ValidationError)
        if not self.location:
            frappe.throw("Location is required.", frappe.ValidationError)
        duplicate = frappe.db.get_value("AOS Location", {"country": self.country, "location": self.location}, "name")
        if duplicate and duplicate != self.name:
            frappe.throw("Location already exists in this country.", frappe.DuplicateEntryError)
