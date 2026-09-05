# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.localization import validate_country, validate_currency, validate_language
from aos.services.user_preference_service import clear_user_preference_cache, validate_location_preference


class AOSUserPreference(Document):
	"""One canonical country, currency, language, and optional location per user."""

	def _canonicalize_localization_links(self) -> None:
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
		location, error = validate_location_preference(self.location, country=self.country)
		if error:
			frappe.throw("Invalid location for the selected country.", frappe.ValidationError)
		self.location = location

	def _validate_links(self):
		# Frappe validates Links before controller.validate(). Canonicalize first,
		# then preserve Frappe's own referential-integrity validation.
		self._canonicalize_localization_links()
		super()._validate_links()

	def validate(self):
		self.user = str(self.user or "").strip()
		if not self.user:
			frappe.throw("User is required.", frappe.ValidationError)
		self._canonicalize_localization_links()

	def after_insert(self):
		clear_user_preference_cache(self.user)

	def on_update(self):
		clear_user_preference_cache(self.user)

	def on_trash(self):
		clear_user_preference_cache(self.user)
