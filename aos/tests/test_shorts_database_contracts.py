from __future__ import annotations

import ast
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import harden_shorts_subsystem, install_shorts_indexes


class TestShortsDatabaseContracts(FrappeTestCase):
    def test_shorts_data_patch_is_idempotent(self):
        harden_shorts_subsystem.execute()
        harden_shorts_subsystem.execute()

    def test_shorts_indexes_exist_after_migrate(self):
        for doctype, name, _columns, _unique in install_shorts_indexes.INDEX_DEFINITIONS:
            rows = frappe.db.sql(
                """SELECT INDEX_NAME FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s LIMIT 1""",
                (f"tab{doctype}", name),
            )
            self.assertTrue(rows, f"missing Shorts index {name}")

    def test_index_patch_is_registered_after_data_patch_and_schema_only(self):
        patches = Path(frappe.get_app_path("aos", "patches.txt")).read_text(encoding="utf-8")
        data_patch = "aos.patches.v1_0.harden_shorts_subsystem"
        index_patch = "aos.patches.v1_0.install_shorts_indexes"
        self.assertIn(data_patch, patches)
        self.assertIn(index_patch, patches)
        self.assertLess(patches.index(data_patch), patches.index(index_patch))

        source = Path(install_shorts_indexes.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"commit", "rollback", "set_value", "insert", "save", "delete"}:
                forbidden_calls.append(f"{node.func.attr}@{node.lineno}")
            if node.func.attr == "sql" and node.args:
                query_node = node.args[0]
                literal = ""
                if isinstance(query_node, ast.Constant) and isinstance(query_node.value, str):
                    literal = query_node.value
                elif isinstance(query_node, ast.JoinedStr):
                    literal = "".join(
                        value.value
                        for value in query_node.values
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
                if literal.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE")):
                    forbidden_calls.append(f"sql-dml@{node.lineno}")
        self.assertEqual(forbidden_calls, [])
        self.assertNotIn("_install_indexes()", Path(harden_shorts_subsystem.__file__).read_text())

    def test_active_uniqueness_fields_are_fixed_size_and_not_public(self):
        for doctype, fieldname in (
            ("AOS Short Report", "active_key"),
            ("AOS Short Repost", "active_key"),
            ("AOS Video Processing Job", "active_key"),
            ("AOS Short Event", "event_key"),
        ):
            field = frappe.get_meta(doctype).get_field(fieldname)
            self.assertIsNotNone(field)
            self.assertTrue(field.hidden)
            self.assertTrue(field.read_only)
            self.assertLessEqual(int(field.length or 140), 64)

    def test_processing_generation_field_exists(self):
        field = frappe.get_meta("AOS Video Processing Job").get_field("generation")
        self.assertIsNotNone(field)
        self.assertEqual(field.fieldtype, "Int")
