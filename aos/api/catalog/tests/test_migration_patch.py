from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from aos.patches.v1_0 import harden_catalog_subsystem


class TestCatalogMigrationPatch(TestCase):
    def test_patch_uses_frappe_safe_indexes_and_bounded_selects(self):
        with (
            patch.object(harden_catalog_subsystem.frappe.db, "table_exists", return_value=True),
            patch.object(harden_catalog_subsystem.frappe.db, "has_column", return_value=True),
            patch.object(harden_catalog_subsystem, "_index_exists", return_value=False),
            patch.object(harden_catalog_subsystem.frappe.db, "add_index") as add_index,
            patch.object(harden_catalog_subsystem.frappe.db, "sql", return_value=[]) as sql,
            patch.object(harden_catalog_subsystem.frappe, "logger") as logger,
        ):
            harden_catalog_subsystem.execute()

        self.assertEqual(add_index.call_count, len(harden_catalog_subsystem.INDEXES))
        self.assertEqual(sql.call_count, 3)
        for index_call in add_index.call_args_list:
            self.assertIsInstance(index_call.args[1], list)
            self.assertIn(index_call.kwargs["index_name"], harden_catalog_subsystem.INDEXES)
        for sql_call in sql.call_args_list:
            statement = str(sql_call.args[0]).lstrip().upper()
            self.assertTrue(statement.startswith("SELECT NAME"))
            self.assertIn("LIMIT %S", statement)
            self.assertNotIn("ALTER TABLE", statement)
        logger.assert_called_once()

    def test_normalization_updates_one_bounded_batch_without_commit(self):
        results = [[{"name": "CAT-1"}, {"name": "CAT-2"}], None, []]
        with (
            patch.object(harden_catalog_subsystem.frappe.db, "sql", side_effect=results) as sql,
            patch.object(harden_catalog_subsystem.frappe.db, "commit", create=True) as commit,
        ):
            harden_catalog_subsystem._normalize_in_batches(
                doctype="AOS Category",
                field="sort_order",
                value=0,
                where_sql="sort_order IS NULL OR sort_order < 0",
            )

        self.assertEqual(sql.call_count, 3)
        update = sql.call_args_list[1]
        self.assertTrue(str(update.args[0]).lstrip().upper().startswith("UPDATE"))
        self.assertEqual(update.args[1], (0, "CAT-1", "CAT-2"))
        commit.assert_not_called()

    def test_unknown_migration_field_is_rejected(self):
        with self.assertRaises(ValueError):
            harden_catalog_subsystem._normalize_in_batches(
                doctype="AOS Category",
                field="owner",
                value="Administrator",
                where_sql="1 = 1",
            )

    def test_missing_tables_are_safe_noops(self):
        with (
            patch.object(harden_catalog_subsystem.frappe.db, "table_exists", return_value=False),
            patch.object(harden_catalog_subsystem.frappe.db, "add_index") as add_index,
            patch.object(harden_catalog_subsystem.frappe.db, "sql") as sql,
            patch.object(harden_catalog_subsystem.frappe, "logger"),
        ):
            harden_catalog_subsystem.execute()
        add_index.assert_not_called()
        sql.assert_not_called()
