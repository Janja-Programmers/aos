# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSWishlist(Document):
	def autoname(self):
		if not self.user or not self.ad:
			frappe.throw("User and Ad are required to generate the name")

		self.name = f"{self.user}-{self.ad}"
