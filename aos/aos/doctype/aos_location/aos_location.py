# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document

from aos.services.localization_service import clear_localization_cache, validate_country

LOCATION_MAX_LENGTH = 140


class AOSLocation(Document):
	def _validate_links(self):
		"""Canonicalize a country code before Frappe validates Link fields.

		``Document.insert`` validates Link fields before the controller's normal
		``validate`` hook.  Without this override, an accepted ISO country code
		(e.g. ``ke``) is rejected as a missing ``Country`` document before the
		localization service can resolve it to the canonical Country name.
		"""

		country, error = validate_country(self.country)
		if error:
			frappe.throw("Invalid country.", frappe.ValidationError)
		self.country = country
		super()._validate_links()

	def validate(self):
		country, error = validate_country(self.country)
		if error:
			frappe.throw("Invalid country.", frappe.ValidationError)
		self.country = country

		self.location = " ".join(str(self.location or "").split())
		if not self.location:
			frappe.throw("Location is required.", frappe.ValidationError)
		if len(self.location) > LOCATION_MAX_LENGTH:
			frappe.throw(
				f"Location must not exceed {LOCATION_MAX_LENGTH} characters.",
				frappe.ValidationError,
			)

		duplicate = frappe.db.get_value(
			"AOS Location",
			{"country": self.country, "location": self.location},
			"name",
		)
		if duplicate and duplicate != self.name:
			frappe.throw(
				"Location already exists in this country.",
				frappe.DuplicateEntryError,
			)

	def after_insert(self):
		clear_localization_cache()

	def on_update(self):
		clear_localization_cache()

	def on_trash(self):
		clear_localization_cache()
