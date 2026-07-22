# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.api.localization.bundle import get_locale_bundle_impl
from aos.api.shared.responses import fail
from aos.tests.feature_test_helpers import AOSFeatureTestMixin
from aos.utils.aos_settings import AOS_SETTINGS_CACHE_KEY, get_aos_settings_snapshot

# On IntegrationTestCase, the doctype test records and all
# link-field test record dependencies are recursively loaded.
EXTRA_TEST_RECORD_DEPENDENCIES = []
IGNORE_TEST_RECORD_DEPENDENCIES = []


class IntegrationTestAOSSettings(AOSFeatureTestMixin, IntegrationTestCase):
	"""Integration tests for validated localization defaults and cache invalidation."""

	def setUp(self):
		self.prefix = self.make_prefix("aos-settings")
		self.created_users: list[str] = []
		frappe.set_user("Administrator")
		self.configure_test_localization_defaults()

	def tearDown(self):
		self.cleanup_feature_rows()
		frappe.set_user("Administrator")

	def test_valid_settings_save_and_clear_snapshot_cache(self):
		settings = frappe.get_single("AOS Settings")
		get_aos_settings_snapshot()
		self.assertIsNotNone(frappe.cache().get_value(AOS_SETTINGS_CACHE_KEY))
		settings.save(ignore_permissions=True)
		self.assertIsNone(frappe.cache().get_value(AOS_SETTINGS_CACHE_KEY))

	def test_invalid_default_country_is_rejected(self):
		settings = frappe.get_single("AOS Settings")
		original = settings.default_country
		settings.default_country = "Missing Localization Country"
		try:
			with self.assertRaises(frappe.ValidationError):
				settings.save(ignore_permissions=True)
		finally:
			settings.default_country = original

	def test_disabled_default_currency_is_rejected(self):
		settings = frappe.get_single("AOS Settings")
		with patch(
			"aos.aos.doctype.aos_settings.aos_settings.validate_currency",
			return_value=(None, fail("Currency is disabled.", error="DISABLED_CURRENCY")),
		):
			with self.assertRaises(frappe.ValidationError):
				settings.validate()

	def test_disabled_default_language_is_rejected(self):
		settings = frappe.get_single("AOS Settings")
		with patch(
			"aos.aos.doctype.aos_settings.aos_settings.validate_language",
			return_value=(None, fail("Language is disabled.", error="DISABLED_LANGUAGE")),
		):
			with self.assertRaises(frappe.ValidationError):
				settings.validate()

	def test_missing_localization_defaults_are_rejected(self):
		for fieldname in ("default_country", "default_currency", "default_language"):
			settings = frappe.get_single("AOS Settings")
			original = getattr(settings, fieldname)
			setattr(settings, fieldname, None)
			try:
				with self.assertRaises(frappe.ValidationError):
					settings.validate()
			finally:
				setattr(settings, fieldname, original)

	def test_locale_bundle_reads_updated_default_after_cache_clear(self):
		settings = frappe.get_single("AOS Settings")
		alternative = frappe.db.get_value(
			"Country",
			{"name": ["!=", settings.default_country]},
			"name",
		)
		if not alternative:
			self.skipTest("A second country is required")

		original = settings.default_country
		try:
			get_aos_settings_snapshot()
			settings.reload()
			settings.default_country = alternative
			settings.save(ignore_permissions=True)
			with patch("aos.api.localization.bundle.rate_limit", return_value=None):
				response = get_locale_bundle_impl()
			self.assertTrue(response.get("ok"), response)
			self.assertEqual(response["data"]["defaults"]["country"], alternative)
		finally:
			settings.reload()
			settings.default_country = original
			settings.save(ignore_permissions=True)
