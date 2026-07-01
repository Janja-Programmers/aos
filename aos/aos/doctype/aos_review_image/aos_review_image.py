# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSReviewImage(Document):
    def validate(self):
        if not (self.media or self.image):
            frappe.throw("Review image requires media or image URL")
