from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.auth.account_helpers import create_aos_profile, create_user_preference
from aos.services.accounts.repository import AccountRepository
from aos.services.localization.preferences import get_user_preference_for_update


class AccountsConcurrencyContracts(IntegrationTestCase):
    def test_profile_mutations_use_database_row_lock(self):
        with patch("aos.services.accounts.repository.frappe.db.sql", return_value=[]) as sql:
            self.assertIsNone(AccountRepository.lock_profile("owner@example.com"))
        query = sql.call_args.args[0].upper().replace("`", "")
        self.assertIn("FOR UPDATE", query)
        self.assertIn("TABAOS PROFILE", query)

    def test_preference_mutations_use_database_row_lock(self):
        with patch("aos.services.localization.preferences.frappe.db.sql", return_value=[]) as sql:
            self.assertIsNone(get_user_preference_for_update("owner@example.com"))
        self.assertIn("FOR UPDATE", sql.call_args.args[0].upper())

    def test_duplicate_profile_bootstrap_returns_database_winner(self):
        candidate = MagicMock()
        candidate.insert.side_effect = frappe.DuplicateEntryError("duplicate")
        winner = MagicMock(name="winner")
        with (
            patch("aos.api.auth.account_helpers.get_profile_for_user", return_value=None),
            patch("aos.api.auth.account_helpers.frappe.db.get_value", return_value="Owner"),
            patch("aos.api.auth.account_helpers.frappe.new_doc", return_value=candidate),
            patch(
                "aos.api.auth.account_helpers.profile_name_for_user",
                return_value="ACC-AAAAAAAAAAAAAAAAAAAA",
            ),
            patch("aos.api.auth.account_helpers.frappe.get_doc", return_value=winner),
        ):
            result = create_aos_profile("owner@example.com")
        self.assertIs(result, winner)
        candidate.insert.assert_called_once_with(ignore_permissions=True)

    def test_duplicate_preference_bootstrap_returns_database_winner(self):
        candidate = MagicMock()
        candidate.insert.side_effect = frappe.DuplicateEntryError("duplicate")
        winner = MagicMock(name="winner")
        with (
            patch("aos.api.auth.account_helpers.get_user_preference", return_value=None),
            patch("aos.api.auth.account_helpers.geo_country_hint", return_value=None),
            patch("aos.api.auth.account_helpers.accept_language_hint", return_value=None),
            patch(
                "aos.api.auth.account_helpers.resolve_guest_context",
                return_value=({"country": "Kenya", "currency": "KES", "language": "en"}, None),
            ),
            patch("aos.api.auth.account_helpers.frappe.new_doc", return_value=candidate),
            patch("aos.api.auth.account_helpers.frappe.db.get_value", return_value="owner@example.com"),
            patch("aos.api.auth.account_helpers.frappe.get_doc", return_value=winner),
        ):
            result, error = create_user_preference("owner@example.com")
        self.assertIsNone(error)
        self.assertIs(result, winner)
        candidate.insert.assert_called_once_with(ignore_permissions=True)

