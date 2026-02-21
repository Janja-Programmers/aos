# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSUserPreference(Document):
	def validate(self):
		if not frappe.db.exists("Country", self.country):
			frappe.throw("Invalid country.")

		if not frappe.db.exists("Language", self.language):
			frappe.throw("Invalid language.")

		if not frappe.db.exists("Currency", self.currency):
			frappe.throw("Invalid currency.")
