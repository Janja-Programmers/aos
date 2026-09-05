from __future__ import annotations

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.accounts.get_my_preference import get_my_preference_impl
from aos.api.accounts.update_my_preference import update_my_preference_impl
from aos.services.user_preference_service import get_user_preference, get_user_preference_for_update, update_user_preference
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class AccountPreferenceIntegrationTests(AOSFeatureTestMixin, IntegrationTestCase):
	def setUp(self):
		self.prefix = self.make_prefix("account-preference")
		self.created_users: list[str] = []
		frappe.set_user("Administrator")
		self.configure_test_localization_defaults()

	def tearDown(self):
		self.cleanup_feature_rows()
		frappe.set_user("Administrator")

	@patch("aos.services.user_preference_service.get_user_preference_for_update")
	def test_partial_update_preserves_unrelated_fields(self, get_locked):
		get_locked.return_value = MagicMock(name="PREF-1", country="Kenya", currency="KES", language="en", get=lambda key: "")
		doc = MagicMock(country="Kenya", currency="KES", language="en", location="")
		with patch("aos.services.user_preference_service.validate_language", return_value=("sw", None)), patch("frappe.get_doc", return_value=doc):
			updated, error = update_user_preference("user@example.com", language="sw")
		self.assertIsNone(error)
		self.assertEqual(updated.country, "Kenya")
		self.assertEqual(updated.currency, "KES")
		self.assertEqual(updated.language, "sw")

	def test_preference_read_uses_current_schema_without_metadata_compatibility_probe(self):
		user = self.make_user("schema-hot-path")
		with patch("aos.services.user_preference_service.frappe.get_meta") as get_meta:
			pref = get_user_preference(user, use_cache=False)
		self.assertIsNotNone(pref)
		get_meta.assert_not_called()

	def test_get_preference_rejects_legacy_or_unknown_fields(self):
		user = self.make_user("strict-read")
		frappe.set_user(user)
		with patch("aos.api.accounts.get_my_preference.rate_limit", return_value=None):
			response = get_my_preference_impl(legacy=True)
		self.assertEqual(response["error"], "INVALID_PROFILE_FIELD")

	def test_preference_update_lock_uses_select_for_update(self):
		with patch("aos.services.user_preference_service.frappe.db.sql", return_value=[]) as sql:
			get_user_preference_for_update("user@example.com")
		query = sql.call_args.args[0]
		self.assertIn("FOR UPDATE", query.upper())
		self.assertIn("WHERE user = %s", query)

	def test_get_missing_preference_does_not_create_state(self):
		user = self.make_user("missing-read", with_preference=False)
		frappe.set_user(user)
		with patch("aos.api.accounts.get_my_preference.rate_limit", return_value=None):
			response = get_my_preference_impl()
		self.assertEqual(response["error"], "PREFERENCE_MISSING")
		self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))

	def test_update_missing_preference_does_not_bootstrap_state(self):
		user = self.make_user("missing-update", with_preference=False)
		frappe.set_user(user)
		with patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None):
			response = update_my_preference_impl(language=self.preference_defaults()[1])
		self.assertEqual(response["error"], "PREFERENCE_MISSING")
		self.assertFalse(frappe.db.exists("AOS User Preference", {"user": user}))

	def test_preference_update_observability_records_localization_field_names(self):
		user = self.make_user("observability")
		frappe.set_user(user)
		currency = self.preference_defaults()[2]
		with (
			patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None),
			patch("aos.api.accounts.update_my_preference.account_log") as account_log,
		):
			response = update_my_preference_impl(currency=currency)
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(account_log.call_args.kwargs["changed_fields"], ["currency"])

	def test_unexpected_update_failure_rolls_back_partial_write(self):
		user = self.make_user("rollback")
		preference_name = frappe.db.get_value("AOS User Preference", {"user": user}, "name")
		original_currency = frappe.db.get_value("AOS User Preference", preference_name, "currency")
		frappe.set_user(user)

		def mutate_then_fail(_user: str, **_kwargs):
			frappe.db.set_value(
				"AOS User Preference",
				preference_name,
				"currency",
				"ROLLBACK_TEST",
				update_modified=False,
			)
			raise RuntimeError("simulated mid-operation failure")

		with (
			patch("aos.api.accounts.update_my_preference.rate_limit", return_value=None),
			patch("aos.api.accounts.update_my_preference.update_user_preference", side_effect=mutate_then_fail),
			patch("aos.api.accounts.update_my_preference.frappe.log_error"),
		):
			response = update_my_preference_impl(currency=original_currency)

		self.assertEqual(response["error"], "INTERNAL_ERROR")
		self.assertEqual(frappe.db.get_value("AOS User Preference", preference_name, "currency"), original_currency)
