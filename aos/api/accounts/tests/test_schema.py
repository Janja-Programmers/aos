from __future__ import annotations

from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

from aos.patches.v1_0 import install_accounts_indexes
from aos.patches.v1_0.install_accounts_indexes import INDEXES


class AccountsSchemaContracts(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        install_accounts_indexes.execute()
        frappe.db.commit()

    def test_required_profile_indexes_are_declared(self):
        self.assertEqual(
            INDEXES,
            {
                "idx_aos_profile_lifecycle": ("AOS Profile", ["account_status", "restore_deadline"]),
                "idx_aos_profile_purge_scan": (
                    "AOS Profile",
                    ["account_status", "purge_status", "restore_deadline"],
                ),
            },
        )

    def test_required_profile_indexes_exist_in_database(self):
        for name, (doctype, fields) in INDEXES.items():
            with self.subTest(name=name):
                self.assertTrue(self._has_exact_index(doctype, tuple(fields), name=name))

    def test_profile_user_link_is_unique_in_database(self):
        self.assertTrue(self._has_exact_index("AOS Profile", ("user",), unique=True))

    def test_schema_index_patch_is_registered(self):
        patches = (Path(__file__).resolve().parents[3] / "patches.txt").read_text()
        self.assertIn("aos.patches.v1_0.install_accounts_indexes", patches)

    def test_public_repository_projection_excludes_private_columns(self):
        repository = (Path(__file__).resolve().parents[3] / "services/accounts/repository.py").read_text()
        public_projection = repository.split('_PUBLIC_PROFILE_FIELDS = """', 1)[1].split('"""', 1)[0]
        for private_column in ("legal_name", "phone", "date_of_birth", "purge_status", "u.email"):
            self.assertNotIn(private_column, public_projection)

    @staticmethod
    def _has_exact_index(
        doctype: str,
        fields: tuple[str, ...],
        *,
        name: str | None = None,
        unique: bool | None = None,
    ) -> bool:
        rows = frappe.db.sql(
            """
            SELECT INDEX_NAME, NON_UNIQUE,
                   GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_csv
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
            GROUP BY INDEX_NAME, NON_UNIQUE
            """,
            (f"tab{doctype}",),
            as_dict=True,
        )
        expected = ",".join(fields)
        for row in rows:
            if str(row.columns_csv or "") != expected:
                continue
            if name is not None and str(row.INDEX_NAME) != name:
                continue
            if unique is not None and (int(row.NON_UNIQUE or 0) == 0) != unique:
                continue
            return True
        return False

