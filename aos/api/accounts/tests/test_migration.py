from __future__ import annotations

from pathlib import Path
import unittest


_AOS_ROOT = Path(__file__).resolve().parents[3]


class AccountsMigrationContracts(unittest.TestCase):
    """Fresh-site Accounts schema invariants stay wired to the current installer."""

    def test_accounts_schema_installer_is_reasserted_after_model_sync(self):
        source = (_AOS_ROOT / "migrate.py").read_text(encoding="utf-8")
        self.assertIn("install_accounts_indexes.execute", source)
        self.assertIn("_SCHEMA_INVARIANT_INSTALLERS", source)

    def test_accounts_patch_is_registered_in_post_model_sync(self):
        patches = (_AOS_ROOT / "patches.txt").read_text(encoding="utf-8")
        marker = "[post_model_sync]"
        self.assertIn(marker, patches)
        post_model_sync = patches.split(marker, 1)[1]
        self.assertIn("aos.patches.v1_0.install_accounts_indexes", post_model_sync)

    def test_accounts_installer_is_schema_only(self):
        source = (_AOS_ROOT / "patches/v1_0/install_accounts_indexes.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "rename_field(",
            "delete_doc(",
            "frappe.db.set_value(",
            'frappe.db.sql("UPDATE',
            'frappe.db.sql("DELETE',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
