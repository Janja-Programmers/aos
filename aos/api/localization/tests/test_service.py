from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from aos.services.localization_service import (
	LOCALE_BUNDLE_SCHEMA_VERSION,
	MAX_ACCEPT_LANGUAGE_ITEMS,
	accept_language_candidates,
	clear_localization_cache,
	country_code_to_flag,
	get_enabled_currencies,
	get_enabled_languages,
	get_locale_bundle_payload,
	resolve_guest_context,
	validate_country,
	validate_currency,
	validate_language,
)


class TestLocalizationService(IntegrationTestCase):
	def setUp(self):
		clear_localization_cache()

	def tearDown(self):
		clear_localization_cache()

	def test_country_flag_uses_regional_indicators(self):
		self.assertEqual(country_code_to_flag("KE"), "🇰🇪")
		self.assertEqual(country_code_to_flag("us"), "🇺🇸")
		self.assertIsNone(country_code_to_flag("KEN"))

	def test_country_accepts_name_and_code(self):
		country = frappe.db.get_value(
			"Country",
			{"code": ["is", "set"]},
			["name", "code"],
			as_dict=True,
		)
		if not country:
			self.skipTest("Country master has no coded record")
		self.assertEqual(validate_country(country.name)[0], country.name)
		self.assertEqual(validate_country(country.code)[0], country.name)

	def test_oversized_or_unknown_identifiers_are_rejected(self):
		self.assertEqual(validate_country("x" * 200)[1]["error"], "INVALID_COUNTRY")
		self.assertEqual(validate_currency("x" * 50)[1]["error"], "INVALID_CURRENCY")
		self.assertEqual(validate_language("x" * 200)[1]["error"], "INVALID_LANGUAGE")

	def test_enabled_master_lists_exclude_disabled_rows(self):
		currencies = get_enabled_currencies()
		languages = get_enabled_languages()
		if frappe.get_meta("Currency").has_field("enabled"):
			self.assertTrue(all(frappe.db.get_value("Currency", row["id"], "enabled") for row in currencies))
		if frappe.get_meta("Language").has_field("enabled"):
			self.assertTrue(all(frappe.db.get_value("Language", row["id"], "enabled") for row in languages))

	def test_accept_language_candidates_include_exact_and_primary(self):
		self.assertEqual(accept_language_candidates("en-US,en;q=0.9"), ["en-US", "en"])

	def test_accept_language_canonicalizes_and_respects_quality(self):
		self.assertEqual(
			accept_language_candidates("fr_ca;q=0.8, EN-us;q=1, xx;q=oops"),
			["en-US", "fr-CA", "en", "fr"],
		)

	def test_accept_language_is_bounded_and_rejects_invalid_quality(self):
		header = ",".join(f"aa-{index};q=0.5" for index in range(MAX_ACCEPT_LANGUAGE_ITEMS + 10))
		self.assertLessEqual(len(accept_language_candidates(header)), MAX_ACCEPT_LANGUAGE_ITEMS * 2)
		self.assertEqual(accept_language_candidates("en;q=2,fr;q=-1"), [])

	def test_guest_context_keeps_preferences_independent(self):
		defaults, error = get_locale_bundle_payload(use_cache=False)
		self.assertIsNone(error)
		country = defaults["defaults"]["country"]
		currency = defaults["defaults"]["currency"]
		language = defaults["defaults"]["language"]

		context, error = resolve_guest_context(
			country=country,
			currency=currency,
			language=language,
			geo_country="invalid",
			accept_language="invalid",
		)
		self.assertIsNone(error)
		self.assertEqual(context["country"], country)
		self.assertEqual(context["currency"], currency)
		self.assertEqual(context["language"], language)
		self.assertEqual(
			context["sources"],
			{"country": "request", "currency": "request", "language": "request"},
		)

	def test_settings_and_bundle_fall_back_when_cache_is_unavailable(self):
		with patch("aos.services.localization_service.frappe.cache", side_effect=RuntimeError("redis down")):
			payload, error = get_locale_bundle_payload(use_cache=True)
		self.assertIsNone(error)
		self.assertEqual(payload["schema_version"], LOCALE_BUNDLE_SCHEMA_VERSION)
		self.assertTrue(payload["countries"])

	def test_bundle_is_versioned_and_served_from_cache(self):
		first, error = get_locale_bundle_payload(use_cache=False)
		self.assertIsNone(error)
		self.assertEqual(first["schema_version"], LOCALE_BUNDLE_SCHEMA_VERSION)
		self.assertGreater(first["cache_ttl_seconds"], 0)

		with patch("aos.services.localization_service.frappe.get_all") as get_all:
			second, error = get_locale_bundle_payload(use_cache=True)
		self.assertIsNone(error)
		self.assertEqual(second, first)
		get_all.assert_not_called()
