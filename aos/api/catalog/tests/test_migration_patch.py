from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

import aos.migrate as migrate
from aos.patches.v1_0 import install_catalog_indexes


class TestCatalogIndexInstaller(TestCase):
    def test_catalog_installer_is_after_migrate_schema_invariant(self):
        self.assertIn(install_catalog_indexes.execute, migrate._SCHEMA_INVARIANT_INSTALLERS)

    def test_installer_uses_frappe_index_apis(self):
        with (
            patch.object(install_catalog_indexes, "_ensure_columns"),
            patch.object(install_catalog_indexes, "_index_definition", return_value=None),
            patch.object(install_catalog_indexes, "_assert_unique_ready") as unique_ready,
            patch.object(install_catalog_indexes.frappe.db, "add_index") as add_index,
            patch.object(install_catalog_indexes.frappe.db, "add_unique") as add_unique,
        ):
            install_catalog_indexes.execute()

        non_unique_count = sum(not row[3] for row in install_catalog_indexes.INDEXES)
        unique_count = sum(row[3] for row in install_catalog_indexes.INDEXES)
        self.assertEqual(add_index.call_count, non_unique_count)
        self.assertEqual(add_unique.call_count, unique_count)
        self.assertEqual(unique_ready.call_count, unique_count)

    def test_exact_existing_index_is_idempotent_noop(self):
        columns = ("sort_order", "category_name", "name")
        with (
            patch.object(install_catalog_indexes, "_ensure_columns"),
            patch.object(install_catalog_indexes, "_index_definition", return_value=(columns, False)),
            patch.object(install_catalog_indexes, "_drop_static_index") as drop_index,
            patch.object(install_catalog_indexes.frappe.db, "add_index") as add_index,
        ):
            install_catalog_indexes._ensure_index(
                "AOS Category", "idx_catalog_order", columns, unique=False
            )
        drop_index.assert_not_called()
        add_index.assert_not_called()

    def test_malformed_named_index_is_reasserted(self):
        columns = ("category", "name")
        with (
            patch.object(install_catalog_indexes, "_ensure_columns"),
            patch.object(install_catalog_indexes, "_index_definition", return_value=(("category",), False)),
            patch.object(install_catalog_indexes, "_drop_static_index") as drop_index,
            patch.object(install_catalog_indexes.frappe.db, "add_index") as add_index,
        ):
            install_catalog_indexes._ensure_index(
                "AOS Ad", "idx_catalog_ad_category_reference", columns, unique=False
            )
        drop_index.assert_called_once()
        add_index.assert_called_once_with(
            "AOS Ad", list(columns), index_name="idx_catalog_ad_category_reference"
        )

    def test_unique_index_checks_duplicate_readiness_before_install(self):
        with (
            patch.object(install_catalog_indexes, "_ensure_columns"),
            patch.object(install_catalog_indexes, "_index_definition", return_value=None),
            patch.object(install_catalog_indexes, "_assert_unique_ready") as unique_ready,
            patch.object(install_catalog_indexes.frappe.db, "add_unique") as add_unique,
        ):
            install_catalog_indexes._ensure_index(
                "AOS Category Attribute Row",
                "uq_catalog_category_attribute",
                ("parent", "parenttype", "parentfield", "attribute"),
                unique=True,
            )
        unique_ready.assert_called_once()
        add_unique.assert_called_once()
