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

    def test_after_migrate_reasserts_current_manual_index_installers(self):
        path = ROOT / "aos" / "migrate.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)

        self.assertIn("def after_migrate()", source)
        required = {
            "install_localization_schema.execute",
            "install_accounts_indexes.execute",
            "harden_media_subsystem.execute",
            "install_shorts_indexes.execute",
            "install_live_indexes.execute",
            "install_report_indexes.execute",
            "install_activity_indexes.execute",
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
