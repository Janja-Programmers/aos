from __future__ import annotations

from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from aos.patches.v1_0.harden_accounts_subsystem import execute


class AccountsMigrationTests(IntegrationTestCase):
    @patch("aos.patches.v1_0.harden_accounts_subsystem.frappe.db.table_exists", return_value=False)
    def test_patch_is_safe_when_tables_are_missing(self, _exists):
        execute()
