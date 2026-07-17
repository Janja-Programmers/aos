from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.accounts.get_my_preference import get_my_preference_impl
from aos.api.accounts.update_my_preference import update_my_preference_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAccountPreferences(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("account-preferences")
        self.created_users: list[str] = []
        self.user = self.make_user("user")
        frappe.set_user(self.user)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_get_returns_rich_independent_preferences(self):
        with patch("aos.api.accounts.get_my_preference.rate_limit", return_value=None):
            response = get_my_preference_impl()
        self.assertTrue(response.get("ok"), response)
        data = response["data"]
        self.assertIn("code", data["country"])
        self.assertIn("flag", data["country"])
        self.assertIn("symbol", data["currency"])
        self.assertIn("code", data["language"])

    def test_currency_update_does_not_change_country_or_language(self):
        preference = frappe.get_doc("AOS User Preference", self.user)
        alternatives = [row.name for row in frappe.get_all("Currency", fields=["name"], filters={"enabled": 1} if frappe.get_meta("Currency").has_field("enabled") else {}) if row.name != preference.currency]
        if not alternatives:
            self.skipTest("A second enabled currency is required")
        original = (preference.country, preference.language)
        with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
            response = update_my_preference_impl(currency=alternatives[0])
        self.assertTrue(response.get("ok"), response)
        preference.reload()
        self.assertEqual((preference.country, preference.language), original)

    def test_language_update_does_not_change_country_or_currency(self):
        preference = frappe.get_doc("AOS User Preference", self.user)
        alternatives = [row.name for row in frappe.get_all("Language", fields=["name"], filters={"enabled": 1} if frappe.get_meta("Language").has_field("enabled") else {}) if row.name != preference.language]
        if not alternatives:
            self.skipTest("A second enabled language is required")
        original = (preference.country, preference.currency)
        with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
            response = update_my_preference_impl(language=alternatives[0])
        self.assertTrue(response.get("ok"), response)
        preference.reload()
        self.assertEqual((preference.country, preference.currency), original)
