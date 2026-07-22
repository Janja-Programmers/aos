from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.localization.bundle import get_locale_bundle_impl
from aos.api.localization.context import resolve_preference_context_impl
from aos.api.localization.locations import get_locations_impl
from aos.services.localization_service import clear_localization_cache
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestLocalizationAPI(AOSFeatureTestMixin, FrappeTestCase):
	def setUp(self):
		self.prefix = self.make_prefix("localization-api")
		self.created_users: list[str] = []
		frappe.set_user("Administrator")
		self.configure_test_localization_defaults()
		clear_localization_cache()
		frappe.set_user("Guest")

	def tearDown(self):
		self.cleanup_feature_rows()
		clear_localization_cache()
		frappe.set_user("Administrator")

	def test_bundle_returns_complete_enabled_master_data_and_defaults(self):
		with patch("aos.api.localization.bundle.rate_limit", return_value=None):
			response = get_locale_bundle_impl()
		self.assertTrue(response.get("ok"), response)
		data = response["data"]
		self.assertEqual(len(data["countries"]), frappe.db.count("Country"))
		self.assertTrue(all(row["enabled"] for row in data["currencies"]))
		self.assertTrue(all(row["enabled"] for row in data["languages"]))
		self.assertEqual(
			data["defaults"]["country"],
			frappe.db.get_single_value("AOS Settings", "default_country"),
		)
		self.assertIn("schema_version", data)
		self.assertGreater(data["cache_ttl_seconds"], 0)

	def test_guest_context_accepts_independent_explicit_values(self):
		country, language, currency = self.preference_defaults()
		with patch("aos.api.localization.context.rate_limit", return_value=None):
			response = resolve_preference_context_impl(
				country=country,
				currency=currency,
				language=language,
			)
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["country"]["id"], country)
		self.assertEqual(response["data"]["currency"]["id"], currency)
		self.assertEqual(response["data"]["language"]["id"], language)
		self.assertEqual(
			response["data"]["sources"],
			{"country": "request", "currency": "request", "language": "request"},
		)

	def test_guest_context_uses_geo_and_accept_language_hints_only_for_missing_fields(self):
		country, language, currency = self.preference_defaults()
		country_code = frappe.db.get_value("Country", country, "code")
		language_code = frappe.db.get_value("Language", language, "language_code")
		if not country_code or not language_code:
			self.skipTest("Coded country and language are required")

		with (
			patch("aos.api.localization.context.rate_limit", return_value=None),
			patch(
				"aos.api.localization.context.geo_country_hint",
				return_value=country_code,
			),
			patch(
				"aos.api.localization.context.accept_language_hint",
				return_value=language_code,
			),
		):
			response = resolve_preference_context_impl(currency=currency)

		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["country"]["id"], country)
		self.assertEqual(response["data"]["language"]["id"], language)
		self.assertEqual(response["data"]["sources"]["country"], "geoip")
		self.assertEqual(response["data"]["sources"]["language"], "accept_language")
		self.assertEqual(response["data"]["sources"]["currency"], "request")

	def test_authenticated_context_uses_stored_preference(self):
		user = self.make_user("context")
		frappe.set_user(user)
		with patch("aos.api.localization.context.rate_limit", return_value=None):
			response = resolve_preference_context_impl(country="invalid override")
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["sources"]["country"], "user_preference")

	def test_locations_filter_inactive_and_use_stable_pagination(self):
		country = self.preference_defaults()[0]
		for label, order, active in (
			(f"{self.prefix} Beta", 2, 1),
			(f"{self.prefix} Alpha", 1, 1),
			(f"{self.prefix} Gamma", 3, 1),
			(f"{self.prefix} Hidden", 0, 0),
		):
			frappe.get_doc(
				{
					"doctype": "AOS Location",
					"country": country,
					"location": label,
					"sort_order": order,
					"is_active": active,
				}
			).insert(ignore_permissions=True)

		with patch("aos.api.localization.locations.rate_limit", return_value=None):
			first = get_locations_impl(country=country, q=self.prefix, limit=2)
		self.assertTrue(first.get("ok"), first)
		self.assertEqual(
			[row["name"] for row in first["data"]["locations"]],
			[f"{self.prefix} Alpha", f"{self.prefix} Beta"],
		)
		self.assertTrue(first["data"]["pagination"]["has_more"])
		self.assertEqual(first["data"]["pagination"]["next_offset"], 2)

		with patch("aos.api.localization.locations.rate_limit", return_value=None):
			second = get_locations_impl(country=country, q=self.prefix, limit=2, offset=2)
		self.assertTrue(second.get("ok"), second)
		self.assertEqual(
			[row["name"] for row in second["data"]["locations"]],
			[f"{self.prefix} Gamma"],
		)
		self.assertFalse(second["data"]["pagination"]["has_more"])
		self.assertIsNone(second["data"]["pagination"]["next_offset"])

	def test_locations_reject_invalid_pagination_and_search(self):
		country = self.preference_defaults()[0]
		with patch("aos.api.localization.locations.rate_limit", return_value=None):
			invalid_limit = get_locations_impl(country=country, limit=0)
			invalid_offset = get_locations_impl(country=country, offset=-1)
			invalid_search = get_locations_impl(country=country, q="x" * 81)
		self.assertEqual(invalid_limit["error"], "INVALID_LIMIT")
		self.assertEqual(invalid_offset["error"], "INVALID_OFFSET")
		self.assertEqual(invalid_search["error"], "INVALID_SEARCH_QUERY")

	def test_valid_country_without_locations_returns_empty_list(self):
		used = set(frappe.get_all("AOS Location", pluck="country"))
		country = next(
			(name for name in frappe.get_all("Country", pluck="name") if name not in used),
			None,
		)
		if not country:
			self.skipTest("Every country has configured locations")
		with patch("aos.api.localization.locations.rate_limit", return_value=None):
			response = get_locations_impl(country=country)
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["locations"], [])
		self.assertFalse(response["data"]["pagination"]["has_more"])

	def test_rate_limit_response_short_circuits_database_work(self):
		limited = {"ok": False, "error": "RATE_LIMIT", "message": "limited", "data": {}}
		with (
			patch("aos.api.localization.bundle.rate_limit", return_value=limited),
			patch("aos.api.localization.bundle.get_locale_bundle_payload") as loader,
		):
			response = get_locale_bundle_impl()
		self.assertEqual(response, limited)
		loader.assert_not_called()
