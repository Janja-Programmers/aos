# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded
# Use these module variables to add/remove to/from that list
EXTRA_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]
IGNORE_TEST_RECORD_DEPENDENCIES = []  # eg. ["User"]


class IntegrationTestAOSLocation(IntegrationTestCase):
	"""Integration tests for country-scoped location invariants."""

	def test_controller_canonicalizes_country_code_and_whitespace(self):
		country = frappe.db.get_value(
			"Country",
			{"code": ["is", "set"]},
			["name", "code"],
			as_dict=True,
		)
		if not country:
			self.skipTest("Country code required")
		label = f"Localization   Canonical   {frappe.generate_hash(length=8)}"
		doc = frappe.get_doc(
			{
				"doctype": "AOS Location",
				"country": country.code.lower(),
				"location": f"  {label}  ",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		try:
			self.assertEqual(doc.country, country.name)
			self.assertEqual(doc.location, " ".join(label.split()))
		finally:
			if frappe.db.exists("AOS Location", doc.name):
				frappe.delete_doc("AOS Location", doc.name, force=True, ignore_permissions=True)

	def test_duplicate_pair_is_rejected_but_other_country_is_allowed(self):
		countries = frappe.get_all("Country", pluck="name", limit=2)
		if len(countries) < 2:
			self.skipTest("Two countries required")
		label = f"Localization Test {frappe.generate_hash(length=8)}"
		first = frappe.get_doc(
			{"doctype": "AOS Location", "country": countries[0], "location": label, "is_active": 1}
		).insert(ignore_permissions=True)
		try:
			with self.assertRaises((frappe.DuplicateEntryError, frappe.ValidationError)):
				frappe.get_doc(
					{"doctype": "AOS Location", "country": countries[0], "location": label, "is_active": 1}
				).insert(ignore_permissions=True)
			second = frappe.get_doc(
				{"doctype": "AOS Location", "country": countries[1], "location": label, "is_active": 1}
			).insert(ignore_permissions=True)
			frappe.delete_doc("AOS Location", second.name, force=True, ignore_permissions=True)
		finally:
			if frappe.db.exists("AOS Location", first.name):
				frappe.delete_doc("AOS Location", first.name, force=True, ignore_permissions=True)
