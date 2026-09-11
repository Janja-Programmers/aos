from __future__ import annotations

import json
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import install_catalog_indexes


class TestCatalogDatabaseContracts(FrappeTestCase):
    def test_catalog_indexes_are_idempotently_present_with_exact_shape(self):
        install_catalog_indexes.execute()
        install_catalog_indexes.execute()

        for doctype, index_name, columns, unique in install_catalog_indexes.INDEXES:
            rows = frappe.db.sql(
                """
                SELECT COLUMN_NAME, NON_UNIQUE
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = %s
                  AND INDEX_NAME = %s
                ORDER BY SEQ_IN_INDEX
                """,
                (f"tab{doctype}", index_name),
                as_dict=True,
            )
            self.assertEqual(
                tuple(row["COLUMN_NAME"] for row in rows),
                columns,
                f"Catalog index {index_name} has the wrong columns",
            )
            self.assertTrue(rows, f"missing Catalog index {index_name}")
            self.assertEqual(
                not bool(int(rows[0]["NON_UNIQUE"])),
                unique,
                f"Catalog index {index_name} has the wrong uniqueness",
            )

    def test_catalog_identity_fields_are_database_backed(self):
        category_meta = frappe.get_meta("AOS Category")
        attribute_meta = frappe.get_meta("AOS Ad Attribute")

        self.assertEqual(category_meta.allow_rename, 0)
        self.assertTrue(category_meta.get_field("category_name").unique)
        self.assertEqual(attribute_meta.allow_rename, 0)
        self.assertTrue(attribute_meta.get_field("label").unique)
        self.assertTrue(attribute_meta.get_field("attribute_key").unique)
        dependency_meta = frappe.get_meta("AOS Category Attribute Dependency Row")
        mapping_key = dependency_meta.get_field("mapping_key")
        self.assertEqual(mapping_key.fieldtype, "Data")
        self.assertEqual(mapping_key.read_only, 1)
        self.assertEqual(mapping_key.hidden, 1)
        self.assertEqual(mapping_key.length, 64)

    def test_catalog_desk_permissions_are_source_controlled_and_role_managed(self):
        schema_locations = {
            "AOS Category": ("aos_category", "aos_category.json"),
            "AOS Ad Attribute": ("aos_ad_attribute", "aos_ad_attribute.json"),
        }
        app_path = Path(frappe.get_app_path("aos"))

        for doctype, (directory, filename) in schema_locations.items():
            with self.subTest(doctype=doctype):
                schema = json.loads(
                    (app_path / "aos" / "doctype" / directory / filename).read_text(encoding="utf-8")
                )
                source_roles = {row["role"] for row in schema["permissions"]}
                effective_roles = {row.role for row in frappe.get_meta(doctype).permissions}

                self.assertTrue(source_roles)
                self.assertTrue(source_roles.issubset(effective_roles))
