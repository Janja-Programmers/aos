from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from aos.services.localization_service import (
    accept_language_candidates,
    country_code_to_flag,
    get_enabled_currencies,
    get_enabled_languages,
    validate_country,
)


class TestLocalizationService(IntegrationTestCase):
    def test_country_flag_uses_regional_indicators(self):
        self.assertEqual(country_code_to_flag("KE"), "🇰🇪")
        self.assertEqual(country_code_to_flag("us"), "🇺🇸")
        self.assertIsNone(country_code_to_flag("KEN"))

    def test_country_accepts_name_and_code(self):
        country = frappe.db.get_value("Country", {"code": ["is", "set"]}, ["name", "code"], as_dict=True)
        if not country:
            self.skipTest("Country master has no coded record")
        self.assertEqual(validate_country(country.name)[0], country.name)
        self.assertEqual(validate_country(country.code)[0], country.name)

    def test_enabled_master_lists_exclude_disabled_rows(self):
        currencies = get_enabled_currencies()
        languages = get_enabled_languages()
        if frappe.get_meta("Currency").has_field("enabled"):
            self.assertTrue(all(frappe.db.get_value("Currency", row["id"], "enabled") for row in currencies))
        if frappe.get_meta("Language").has_field("enabled"):
            self.assertTrue(all(frappe.db.get_value("Language", row["id"], "enabled") for row in languages))

    def test_accept_language_candidates_include_exact_and_primary(self):
        self.assertEqual(accept_language_candidates("en-US,en;q=0.9"), ["en-US", "en"])

    def test_accept_language_respects_quality_and_ignores_invalid_entries(self):
        self.assertEqual(accept_language_candidates("xx;q=oops, fr-CA;q=0.8, en;q=1"), ["en", "fr-CA", "fr"])
