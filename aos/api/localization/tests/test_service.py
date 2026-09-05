from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.services.localization import (
	LOCALIZATION_SCHEMA_VERSION,
	MAX_ACCEPT_LANGUAGE_ITEMS,
	accept_language_candidates,
	clear_localization_cache,
	country_code_to_flag,
	get_locale_bundle_payload,
	resolve_accept_language,
	resolve_guest_context,
	serialize_preference,
	validate_country,
	validate_currency,
	validate_language,
)
from aos.services.localization.repository import LocalizationConfigurationError
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestLocalizationService(AOSFeatureTestMixin, IntegrationTestCase):
	def setUp(self):
		self.prefix = self.make_prefix("localization-service")
		self.created_users: list[str] = []
		frappe.set_user("Administrator")
		self.configure_test_localization_defaults()
		clear_localization_cache()

	def tearDown(self):
		self.cleanup_feature_rows()
		clear_localization_cache()
		frappe.set_user("Administrator")

	def test_country_flag_uses_regional_indicators(self):
		self.assertEqual(country_code_to_flag("KE"), "🇰🇪")
		self.assertEqual(country_code_to_flag("us"), "🇺🇸")
		self.assertIsNone(country_code_to_flag("KEN"))

	def test_country_accepts_name_and_iso_code_and_returns_canonical_name(self):
		country = frappe.db.get_value("Country", {"code": ["is", "set"]}, ["name", "code"], as_dict=True)
		if not country:
			self.skipTest("Country master has no coded record")
		self.assertEqual(validate_country(country.name)[0], country.name)
		self.assertEqual(validate_country(country.code.lower())[0], country.name)

	def test_oversized_or_unknown_identifiers_are_rejected(self):
		self.assertEqual(validate_country("x" * 200)[1]["error"], "INVALID_COUNTRY")
		self.assertEqual(validate_currency("x" * 50)[1]["error"], "INVALID_CURRENCY")
		self.assertEqual(validate_language("x" * 200)[1]["error"], "INVALID_LANGUAGE")

	def test_accept_language_candidates_include_exact_and_primary(self):
		self.assertEqual(accept_language_candidates("en-US,en;q=0.9"), ["en-US", "en"])

	def test_accept_language_rejects_nonstandard_underscore_tags(self):
		self.assertEqual(accept_language_candidates("en_US,en;q=0.8"), ["en"])

	def test_accept_language_is_bounded_and_invalid_quality_is_dropped(self):
		header = ",".join(f"aa-{index};q=0.5" for index in range(MAX_ACCEPT_LANGUAGE_ITEMS + 10))
		self.assertLessEqual(len(accept_language_candidates(header)), MAX_ACCEPT_LANGUAGE_ITEMS * 2)
		self.assertEqual(accept_language_candidates("en;q=2,fr;q=-1"), [])

	def test_accept_language_resolution_uses_one_bounded_candidate_query(self):
		row = frappe._dict({"name": "en", "language_name": "English", "language_code": "en", "enabled": 1})
		with patch("aos.services.localization.validators.languages_matching_candidates", return_value=[row]) as lookup:
			resolved = resolve_accept_language("en-US,en;q=0.9")
		self.assertEqual(resolved, "en")
		lookup.assert_called_once_with(["en-US", "en"])

	def test_explicit_language_does_not_accept_display_name_alias(self):
		row = frappe.db.get_value(
			"Language",
			{},
			["name", "language_name"],
			order_by="name asc",
			as_dict=True,
		)
		if not row or not row.language_name or row.name == row.language_name:
			self.skipTest("A distinct Language display name is required")
		_value, error = validate_language(row.language_name)
		self.assertEqual(error["error"], "INVALID_LANGUAGE")

	def test_guest_context_keeps_preferences_independent(self):
		bundle, error = get_locale_bundle_payload(use_cache=False)
		self.assertIsNone(error)
		defaults = bundle["defaults"]
		context, error = resolve_guest_context(
			country=defaults["country"],
			currency=defaults["currency"],
			language=defaults["language"],
			geo_country="invalid",
			accept_language="invalid",
		)
		self.assertIsNone(error)
		self.assertEqual(context["sources"], {"country": "request", "currency": "request", "language": "request"})

	def test_preference_serializer_is_id_only_and_performs_no_master_lookups(self):
		preference = frappe._dict({"country": "Kenya", "currency": "KES", "language": "en", "location": "LOC-1"})
		with patch("aos.services.localization.repository.frappe.db.get_value") as lookup:
			payload = serialize_preference(preference, is_country_locked=True)
		self.assertEqual(
			payload,
			{
				"country": "Kenya",
				"currency": "KES",
				"language": "en",
				"location": "LOC-1",
				"is_country_locked": True,
			},
		)
		lookup.assert_not_called()

	def test_fully_explicit_guest_context_does_not_load_defaults(self):
		country, language, currency = self.preference_defaults()
		with patch("aos.services.localization.service.get_default_preferences") as defaults:
			context, error = resolve_guest_context(country=country, currency=currency, language=language)
		self.assertIsNone(error)
		self.assertEqual(context["country"], country)
		defaults.assert_not_called()

	def test_bundle_falls_back_to_database_when_localization_cache_is_unavailable(self):
		with patch("aos.services.localization.cache.frappe.cache", side_effect=RuntimeError("redis down")):
			payload, error = get_locale_bundle_payload(use_cache=True)
		self.assertIsNone(error)
		self.assertEqual(payload["schema_version"], LOCALIZATION_SCHEMA_VERSION)
		self.assertTrue(payload["countries"])

	def test_bundle_is_versioned_and_served_from_shared_cache(self):
		first, error = get_locale_bundle_payload(use_cache=False)
		self.assertIsNone(error)
		self.assertEqual(first["schema_version"], LOCALIZATION_SCHEMA_VERSION)
		with patch("aos.services.localization.repository.frappe.get_all") as get_all:
			second, error = get_locale_bundle_payload(use_cache=True)
		self.assertIsNone(error)
		self.assertEqual(second, first)
		get_all.assert_not_called()

	def test_bundle_fails_closed_if_reference_table_exceeds_public_bound(self):
		with patch("aos.services.localization.service.list_countries", side_effect=LocalizationConfigurationError("too many")):
			payload, error = get_locale_bundle_payload(use_cache=False)
		self.assertIsNone(payload)
		self.assertEqual(error["error"], "CONFIG_ERROR")
