# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class AOSSellerFollow(Document):
	def after_insert(self):
		frappe.db.sql("""
			UPDATE `tabAOS Seller`
			SET total_followers = total_followers + 1
			WHERE name = %s
		""", (self.seller,))
	
	def on_trash(self):
		frappe.db.sql("""
			UPDATE `tabAOS Seller`
			SET total_followers = GREATEST(total_followers - 1, 0)
			WHERE name = %s
		""", (self.seller,))
