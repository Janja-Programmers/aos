from __future__ import annotations

import ast
import unittest
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import harden_live_subsystem, install_live_indexes


class TestLiveDatabaseContracts(FrappeTestCase):
    def test_live_data_patch_is_idempotent(self):
        harden_live_subsystem.execute()
        harden_live_subsystem.execute()

    def test_live_indexes_exist_after_migrate(self):
        for doctype, name, _columns, _unique in install_live_indexes.INDEXES:
            rows = frappe.db.sql(
                """
                SELECT INDEX_NAME
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND INDEX_NAME=%s
                LIMIT 1
                """,
                (f"tab{doctype}", name),
            )
            self.assertTrue(rows, f"missing Live index {name}")

    def test_data_patch_precedes_schema_patch_and_does_not_call_livekit(self):
        patches = Path(frappe.get_app_path("aos", "patches.txt")).read_text(encoding="utf-8")
        data_patch = "aos.patches.v1_0.harden_live_subsystem"
        index_patch = "aos.patches.v1_0.install_live_indexes"
        self.assertIn(data_patch, patches)
        self.assertIn(index_patch, patches)
        self.assertLess(patches.index(data_patch), patches.index(index_patch))

        source = Path(harden_live_subsystem.__file__).read_text(encoding="utf-8")
        self.assertNotIn("LiveKitAPI", source)
        self.assertNotIn("frappe.enqueue", source)
        self.assertNotIn("frappe.db.commit", source)

    def test_index_patch_is_schema_only(self):
        tree = ast.parse(Path(install_live_indexes.__file__).read_text(encoding="utf-8"))
        forbidden: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr in {"commit", "rollback", "insert", "save", "delete", "set_value"}:
                forbidden.append(f"{node.func.attr}@{node.lineno}")
            if node.func.attr == "sql" and node.args:
                query = node.args[0]
                literal = ""
                if isinstance(query, ast.Constant) and isinstance(query.value, str):
                    literal = query.value
                elif isinstance(query, ast.JoinedStr):
                    literal = "".join(
                        value.value
                        for value in query.values
                        if isinstance(value, ast.Constant) and isinstance(value.value, str)
                    )
                if literal.lstrip().upper().startswith(
                    ("INSERT", "UPDATE", "DELETE", "REPLACE", "TRUNCATE")
                ):
                    forbidden.append(f"sql-dml@{node.lineno}")
        self.assertEqual(forbidden, [])

    def test_database_uniqueness_boundaries_are_present(self):
        expected = (
            ("AOS Live Stream", "active_host_key"),
            ("AOS Live CoHost", "active_workflow_key"),
            ("AOS Live Message", "active_idempotency_key"),
            ("AOS LiveKit Webhook Event", "event_id"),
            ("AOS Notification", "dedupe_key"),
        )
        for doctype, fieldname in expected:
            field = frappe.get_meta(doctype).get_field(fieldname)
            self.assertIsNotNone(field, f"{doctype}.{fieldname}")
            self.assertTrue(field.unique, f"{doctype}.{fieldname} must be unique")

    def test_private_consistency_fields_are_not_public_desk_inputs(self):
        expected = (
            ("AOS Live Stream", "active_host_key"),
            ("AOS Live Stream View", "active_identity_key"),
            ("AOS Live Stream View", "livekit_identity"),
            ("AOS Live CoHost", "active_workflow_key"),
            ("AOS Live CoHost", "livekit_identity"),
            ("AOS Live Message", "active_idempotency_key"),
            ("AOS Notification", "dedupe_key"),
        )
        for doctype, fieldname in expected:
            field = frappe.get_meta(doctype).get_field(fieldname)
            self.assertIsNotNone(field)
            self.assertTrue(field.hidden)
            self.assertTrue(field.read_only)


if __name__ == "__main__":
    unittest.main()
