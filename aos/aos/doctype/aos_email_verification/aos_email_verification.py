# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSEmailVerification(Document):
	def autoname(self):
		if not self.user or not self.purpose:
			frappe.throw("User and Purpose are required to generate the name")

		self.name = f"{self.user}-{self.purpose}"
