# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.localization_service import validate_country, validate_currency, validate_language
from aos.services.user_preference_service import clear_user_preference_cache


class AOSUserPreference(Document):
	"""One canonical country, currency, and language preference per user."""

	def validate(self):
		self._validate_user()
		self._validate_links()

	def after_insert(self):
		clear_user_preference_cache(self.user)

	def on_update(self):
		clear_user_preference_cache(self.user)

	def on_trash(self):
		clear_user_preference_cache(self.user)

	def _validate_user(self):
		self.user = str(self.user or "").strip()
		if not self.user:
			frappe.throw("User is required.", frappe.ValidationError)
		if not frappe.db.exists("User", self.user):
			frappe.throw("Invalid user.", frappe.ValidationError)

	def _validate_links(self):
		checks = (
			("country", self.country, validate_country),
			("currency", self.currency, validate_currency),
			("language", self.language, validate_language),
		)
		for fieldname, value, validator in checks:
			resolved, error = validator(value)
			if error:
				frappe.throw(f"Invalid or disabled {fieldname}.", frappe.ValidationError)
			setattr(self, fieldname, resolved)
