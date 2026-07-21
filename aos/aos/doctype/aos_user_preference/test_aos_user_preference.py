# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.auth.account_helpers import ensure_user_preference
from aos.tests.feature_test_helpers import AOSFeatureTestMixin

EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestAOSUserPreference(AOSFeatureTestMixin, IntegrationTestCase):
	"""Integration tests for AOS User Preference identity invariants."""

	def setUp(self):
		self.prefix = self.make_prefix("aos-user-preference")
		self.created_users: list[str] = []
		frappe.set_user("Administrator")

	def tearDown(self):
		self.cleanup_feature_rows()
		frappe.set_user("Administrator")

	def test_controller_requires_existing_user(self):
		country, language, currency = self.preference_defaults()

		pref = frappe.get_doc(
			{
				"doctype": "AOS User Preference",
				"user": f"{self.prefix}-missing@example.com",
				"country": country,
				"language": language,
				"currency": currency,
			}
		)

		with self.assertRaises(frappe.ValidationError):
			pref.insert(ignore_permissions=True)

	def test_auth_bootstrap_creates_one_preference_idempotently(self):
		user = self.make_user("bootstrap", with_preference=False)
		self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))

		pref, err = ensure_user_preference(user)
		second_pref, second_err = ensure_user_preference(user)

		self.assertIsNone(err)
		self.assertIsNone(second_err)
		self.assertEqual(pref.name, second_pref.name)
		self.assertEqual(
			frappe.db.count("AOS User Preference", {"user": user}),
			1,
		)

	def test_controller_rejects_invalid_market_links(self):
		user = self.make_user("invalid-links", with_preference=False)
		_country, language, currency = self.preference_defaults()

		invalid = frappe.get_doc(
			{
				"doctype": "AOS User Preference",
				"user": user,
				"country": "Missing Country",
				"language": language,
				"currency": currency,
			}
		)

		with self.assertRaises(frappe.ValidationError):
			invalid.insert(ignore_permissions=True)

	def test_controller_canonicalizes_country_code_and_currency_case(self):
		user = self.make_user("canonical-country", with_preference=False)
		country, language, currency = self.preference_defaults()
		country_code = frappe.db.get_value("Country", country, "code")
		if not country_code:
			self.skipTest("Country code required")

		preference = frappe.get_doc(
			{
				"doctype": "AOS User Preference",
				"user": user,
				"country": country_code.lower(),
				"language": language,
				"currency": currency.lower(),
			}
		).insert(ignore_permissions=True)

		self.assertEqual(preference.country, country)
		self.assertEqual(preference.currency, currency)

	def test_database_contract_enforces_one_preference_per_user(self):
		user = self.make_user("unique-pref", with_preference=False)
		country, language, currency = self.preference_defaults()

		first = frappe.get_doc(
			{
				"doctype": "AOS User Preference",
				"user": user,
				"country": country,
				"language": language,
				"currency": currency,
			}
		)
		first.insert(ignore_permissions=True)

		duplicate = frappe.get_doc(
			{
				"doctype": "AOS User Preference",
				"user": user,
				"country": country,
				"language": language,
				"currency": currency,
			}
		)

		with self.assertRaises(frappe.DuplicateEntryError):
			duplicate.insert(ignore_permissions=True)
