from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class TestMigrationHookContracts(unittest.TestCase):
    """Source-level guarantees for schema invariants that must survive upgrades."""

    def test_after_migrate_hook_is_registered(self):
        hooks = (ROOT / "aos" / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('after_migrate = "aos.migrate.after_migrate"', hooks)

    def test_before_tests_reasserts_schema_invariants_after_test_site_sync(self):
        hooks = (ROOT / "aos" / "hooks.py").read_text(encoding="utf-8")
        install = (ROOT / "aos" / "install.py").read_text(encoding="utf-8")
        self.assertIn('before_tests = "aos.install.before_tests"', hooks)
        self.assertIn("def before_tests()", install)
        self.assertIn("from aos.migrate import after_migrate", install)
        self.assertIn("after_migrate()", install)
        self.assertNotIn("frappe.db.commit", install[install.index("def before_tests()"):])

    def test_after_migrate_reasserts_current_manual_index_installers(self):
        path = ROOT / "aos" / "migrate.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        self.assertIn("def after_migrate()", source)
        required = {
            "install_localization_schema.execute",
            "install_accounts_indexes.execute",
            "install_media_indexes.execute",
            "install_verification_indexes.execute",
            "install_shorts_indexes.execute",
            "install_live_indexes.execute",
            "install_call_indexes.execute",
            "install_call_public_indexes.execute",
            "install_review_indexes.execute",
            "install_social_indexes.execute",
            "install_report_indexes.execute",
            "install_activity_indexes.execute",
            "install_wishlist_indexes.execute",
        }
        missing = sorted(item for item in required if item not in source)
        self.assertEqual(missing, [])

        forbidden_calls: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {
                "insert",
                "save",
                "delete",
                "set_value",
                "commit",
                "rollback",
            }:
                forbidden_calls.append(f"{func.attr}@{node.lineno}")
        self.assertEqual(forbidden_calls, [])


if __name__ == "__main__":
    unittest.main()
