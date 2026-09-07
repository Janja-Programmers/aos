from __future__ import annotations

from unittest.mock import patch

from frappe.tests import IntegrationTestCase

from aos.services.accounts.identity import normalize_public_account_id, public_account_id_for_user, resolve_account_reference


class AccountsIdentityIntegrationTests(IntegrationTestCase):
    def test_public_id_format_is_strict(self):
        self.assertEqual(normalize_public_account_id("ACC-AAAAAAAAAAAAAAAAAAAA"), "ACC-AAAAAAAAAAAAAAAAAAAA")
        self.assertEqual(normalize_public_account_id("user@example.com"), "")

    def test_resolve_public_reference_uses_profile_lookup(self):
        with patch("frappe.db.get_value", return_value="user@example.com"):
            self.assertEqual(resolve_account_reference("ACC-AAAAAAAAAAAAAAAAAAAA"), "user@example.com")

    def test_public_id_never_returns_email(self):
        with patch("frappe.db.get_value", return_value="ACC-AAAAAAAAAAAAAAAAAAAA"):
            value = public_account_id_for_user("user@example.com")
        self.assertEqual(value, "ACC-AAAAAAAAAAAAAAAAAAAA")
        self.assertNotIn("@", value)
