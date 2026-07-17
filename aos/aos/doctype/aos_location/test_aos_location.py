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

    def test_duplicate_pair_is_rejected_but_other_country_is_allowed(self):
        countries = frappe.get_all("Country", pluck="name", limit_page_length=2)
        if len(countries) < 2:
            self.skipTest("Two countries required")
        label = f"Localization Test {frappe.generate_hash(length=8)}"
        first = frappe.get_doc({"doctype": "AOS Location", "country": countries[0], "location": label, "is_active": 1}).insert(ignore_permissions=True)
        try:
            with self.assertRaises((frappe.DuplicateEntryError, frappe.ValidationError)):
                frappe.get_doc({"doctype": "AOS Location", "country": countries[0], "location": label, "is_active": 1}).insert(ignore_permissions=True)
            second = frappe.get_doc({"doctype": "AOS Location", "country": countries[1], "location": label, "is_active": 1}).insert(ignore_permissions=True)
            frappe.delete_doc("AOS Location", second.name, force=True, ignore_permissions=True)
        finally:
            if frappe.db.exists("AOS Location", first.name):
                frappe.delete_doc("AOS Location", first.name, force=True, ignore_permissions=True)
