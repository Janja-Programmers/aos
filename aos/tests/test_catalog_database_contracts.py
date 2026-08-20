from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import enforce_catalog_desk_permissions, harden_catalog_subsystem


class TestCatalogDatabaseContracts(FrappeTestCase):
    def test_catalog_indexes_are_idempotently_present(self):
        harden_catalog_subsystem.execute()
        harden_catalog_subsystem.execute()
        for index_name, (doctype, _fields) in harden_catalog_subsystem.INDEXES.items():
            rows = frappe.db.sql(
                """
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND INDEX_NAME = %s
                """,
                (f"tab{doctype}", index_name),
            )
            self.assertTrue(rows, f"missing Catalog index {index_name}")

    def test_catalog_name_fields_remain_unique(self):
        category_meta = frappe.get_meta("AOS Category")
        attribute_meta = frappe.get_meta("AOS Ad Attribute")
        self.assertTrue(category_meta.get_field("category_name").unique)
        self.assertTrue(attribute_meta.get_field("label").unique)

    def test_catalog_desk_permissions_are_role_managed(self):
        before = {
            doctype: frappe.db.count("Custom DocPerm", {"parent": doctype})
            for doctype in enforce_catalog_desk_permissions.CATALOG_ROLE_MANAGED_DOCTYPES
        }

        enforce_catalog_desk_permissions.execute()
        enforce_catalog_desk_permissions.execute()

        for doctype in enforce_catalog_desk_permissions.CATALOG_ROLE_MANAGED_DOCTYPES:
            self.assertEqual(
                frappe.db.count("Custom DocPerm", {"parent": doctype}),
                before[doctype],
                f"catalog permission patch must preserve Role Permissions Manager overrides for {doctype}",
            )
            # Loading metadata after the patch also verifies that the cache clear
            # leaves Frappe's effective permission model usable.
            self.assertTrue(frappe.get_meta(doctype).permissions)
