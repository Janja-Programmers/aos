from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.localization.bundle import get_locale_bundle_impl
from aos.api.localization.context import resolve_locale_context_impl
from aos.api.localization.locations import get_locations_impl
from aos.services.localization import clear_localization_cache
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

	def test_bundle_returns_bounded_canonical_master_contract(self):
		with patch("aos.api.localization.bundle.localization_rate_limit", return_value=None):
			response = get_locale_bundle_impl()
		self.assertTrue(response.get("ok"), response)
		data = response["data"]
		self.assertEqual(data["schema_version"], "2.0")
		self.assertEqual(len(data["countries"]), frappe.db.count("Country"))
		self.assertGreater(data["cache_ttl_seconds"], 0)
		self.assertEqual(set(data), {"schema_version", "cache_ttl_seconds", "countries", "currencies", "languages", "defaults"})
		if data["currencies"]:
			self.assertEqual(set(data["currencies"][0]), {"id", "code", "symbol", "name"})
		if data["languages"]:
			self.assertEqual(set(data["languages"][0]), {"id", "code", "name", "flag"})

	def test_bundle_rejects_unknown_request_fields(self):
		response = get_locale_bundle_impl(legacy=True)
		self.assertEqual(response["error"], "LOCALIZATION_UNKNOWN_FIELD")
		self.assertEqual(response["data"]["fields"], ["legacy"])

	def test_guest_context_accepts_independent_explicit_values(self):
		country, language, currency = self.preference_defaults()
		with patch("aos.api.localization.context.localization_rate_limit", return_value=None):
			response = resolve_locale_context_impl(country=country, currency=currency, language=language)
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["country"], country)
		self.assertEqual(response["data"]["currency"], currency)
		self.assertEqual(response["data"]["language"], language)
		self.assertEqual(response["data"]["sources"], {"country": "request", "currency": "request", "language": "request"})

	def test_guest_context_uses_geo_and_accept_language_hints_only_for_missing_fields(self):
		country, language, currency = self.preference_defaults()
		country_code = frappe.db.get_value("Country", country, "code")
		language_code = frappe.db.get_value("Language", language, "language_code")
		if not country_code or not language_code:
			self.skipTest("Coded country and language are required")
		with (
			patch("aos.api.localization.context.localization_rate_limit", return_value=None),
			patch("aos.api.localization.context.geo_country_hint", return_value=country_code),
			patch("aos.api.localization.context.accept_language_hint", return_value=language_code),
		):
			response = resolve_locale_context_impl(currency=currency)
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["country"], country)
		self.assertEqual(response["data"]["language"], language)
		self.assertEqual(response["data"]["sources"]["country"], "geoip")
		self.assertEqual(response["data"]["sources"]["language"], "accept_language")
		self.assertEqual(response["data"]["sources"]["currency"], "request")

	def test_authenticated_context_uses_stored_preference_without_overrides(self):
		user = self.make_user("context")
		country, language, currency = self.preference_defaults()
		frappe.set_user(user)
		with patch("aos.api.localization.context.localization_rate_limit", return_value=None):
			response = resolve_locale_context_impl()
		self.assertTrue(response.get("ok"), response)
		self.assertEqual(response["data"]["country"], country)
		self.assertEqual(response["data"]["currency"], currency)
		self.assertEqual(response["data"]["language"], language)
		self.assertEqual(set(response["data"]["sources"].values()), {"user_preference"})

	def test_authenticated_context_rejects_request_override(self):
		user = self.make_user("context-override")
		frappe.set_user(user)
		with patch("aos.api.localization.context.localization_rate_limit", return_value=None):
			response = resolve_locale_context_impl(country=self.preference_defaults()[0])
		self.assertEqual(response["error"], "LOCALIZATION_OVERRIDE_NOT_ALLOWED")

	def test_context_rejects_unknown_fields(self):
		response = resolve_locale_context_impl(locale="en")
		self.assertEqual(response["error"], "LOCALIZATION_UNKNOWN_FIELD")

	def test_locations_filter_inactive_and_use_stable_pagination(self):
		country = self.preference_defaults()[0]
		for label, order, active in (
			(f"{self.prefix} Beta", 2, 1),
			(f"{self.prefix} Alpha", 1, 1),
			(f"{self.prefix} Gamma", 3, 1),
			(f"{self.prefix} Hidden", 0, 0),
		):
			frappe.get_doc({"doctype": "AOS Location", "country": country, "location": label, "sort_order": order, "is_active": active}).insert(ignore_permissions=True)
		with patch("aos.api.localization.locations.localization_rate_limit", return_value=None):
			first = get_locations_impl(country=country, q=self.prefix, limit=2)
		self.assertTrue(first.get("ok"), first)
		self.assertEqual(first["data"]["country"], country)
		self.assertEqual([row["name"] for row in first["data"]["locations"]], [f"{self.prefix} Alpha", f"{self.prefix} Beta"])
		self.assertEqual(set(first["data"]["locations"][0]), {"id", "name", "country"})
		self.assertEqual(first["data"]["pagination"]["next_offset"], 2)
		with patch("aos.api.localization.locations.localization_rate_limit", return_value=None):
			second = get_locations_impl(country=country, q=self.prefix, limit=2, offset=2)
		self.assertEqual([row["name"] for row in second["data"]["locations"]], [f"{self.prefix} Gamma"])
		self.assertFalse(second["data"]["pagination"]["has_more"])

	def test_locations_reject_invalid_pagination_search_and_removed_aliases(self):
		country = self.preference_defaults()[0]
		with patch("aos.api.localization.locations.localization_rate_limit", return_value=None):
			invalid_limit = get_locations_impl(country=country, limit=0)
			invalid_offset = get_locations_impl(country=country, offset=-1)
			invalid_search = get_locations_impl(country=country, q="x" * 81)
		self.assertEqual(invalid_limit["error"], "INVALID_LIMIT")
		self.assertEqual(invalid_offset["error"], "INVALID_OFFSET")
		self.assertEqual(invalid_search["error"], "INVALID_SEARCH_QUERY")
		self.assertEqual(get_locations_impl(country=country, start=0)["error"], "LOCALIZATION_UNKNOWN_FIELD")
		self.assertEqual(get_locations_impl(country=country, search=self.prefix)["error"], "LOCALIZATION_UNKNOWN_FIELD")

	def test_authenticated_locations_reject_country_override(self):
		user = self.make_user("locations-override")
		frappe.set_user(user)
		with patch("aos.api.localization.locations.localization_rate_limit", return_value=None):
			response = get_locations_impl(country=self.preference_defaults()[0])
		self.assertEqual(response["error"], "LOCALIZATION_OVERRIDE_NOT_ALLOWED")

	def test_unexpected_errors_are_logged_and_not_exposed(self):
		country = self.preference_defaults()[0]
		with (
			patch("aos.api.localization.locations.localization_rate_limit", return_value=None),
			patch("aos.api.localization.locations.get_locations_page", side_effect=RuntimeError("sql password=secret")),
			patch("aos.api.localization.locations.frappe.log_error") as log_error,
		):
			response = get_locations_impl(country=country)
		self.assertEqual(response["error"], "INTERNAL_ERROR")
		self.assertNotIn("secret", str(response))
		log_error.assert_called_once()

	def test_rate_limiter_unavailability_fails_open_for_reference_reads(self):
		from aos.api.localization.throttle import localization_rate_limit

		with patch("aos.api.localization.throttle.rate_limit", side_effect=RuntimeError("redis unavailable")):
			response = localization_rate_limit(endpoint="bundle", limit=1, message="limited")
		self.assertIsNone(response)

	def test_rate_limit_response_short_circuits_database_work(self):
		limited = {"ok": False, "error": "RATE_LIMIT", "message": "limited", "data": {}}
		with (
			patch("aos.api.localization.bundle.localization_rate_limit", return_value=limited),
			patch("aos.api.localization.bundle.get_locale_bundle_payload") as loader,
		):
			response = get_locale_bundle_impl()
		self.assertEqual(response, limited)
		loader.assert_not_called()

	def test_removed_resolve_preference_context_endpoint_is_absent(self):
		from aos.api.v1 import localization as v1_localization

		self.assertFalse(hasattr(v1_localization, "resolve_preference_context"))
