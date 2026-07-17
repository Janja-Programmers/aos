from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.localization.bundle import get_locale_bundle_impl
from aos.api.localization.context import resolve_preference_context_impl
from aos.api.localization.locations import get_locations_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestLocalizationAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("localization-api")
        self.created_users: list[str] = []
        frappe.set_user("Guest")

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def test_bundle_returns_complete_enabled_master_data_and_defaults(self):
        with patch("aos.api.localization.bundle.rate_limit", return_value=None):
            response = get_locale_bundle_impl()
        self.assertTrue(response.get("ok"), response)
        data = response["data"]
        self.assertEqual(len(data["countries"]), frappe.db.count("Country"))
        self.assertTrue(all(row["enabled"] for row in data["currencies"]))
        self.assertTrue(all(row["enabled"] for row in data["languages"]))
        self.assertEqual(data["defaults"]["country"], frappe.db.get_single_value("AOS Settings", "default_country"))

    def test_guest_context_accepts_independent_explicit_values(self):
        country, language, currency = self.preference_defaults()
        with patch("aos.api.localization.context.rate_limit", return_value=None):
            response = resolve_preference_context_impl(country=country, currency=currency, language=language)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["country"]["id"], country)
        self.assertEqual(response["data"]["currency"]["id"], currency)
        self.assertEqual(response["data"]["language"]["id"], language)
        self.assertEqual(response["data"]["sources"], {"country": "request", "currency": "request", "language": "request"})

    def test_authenticated_context_uses_stored_preference(self):
        user = self.make_user("context")
        frappe.set_user(user)
        with patch("aos.api.localization.context.rate_limit", return_value=None):
            response = resolve_preference_context_impl(country="invalid override")
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["sources"]["country"], "user_preference")

    def test_locations_filter_search_limit_inactive_and_stable_order(self):
        country = self.preference_defaults()[0]
        rows = []
        for label, order, active in ((f"{self.prefix} Beta", 2, 1), (f"{self.prefix} Alpha", 1, 1), (f"{self.prefix} Hidden", 0, 0)):
            rows.append(frappe.get_doc({"doctype": "AOS Location", "country": country, "location": label, "sort_order": order, "is_active": active}).insert(ignore_permissions=True))
        with patch("aos.api.localization.locations.rate_limit", return_value=None):
            response = get_locations_impl(country=country, q=self.prefix, limit=1)
        self.assertTrue(response.get("ok"), response)
        locations = response["data"]["locations"]
        self.assertEqual(len(locations), 1)
        self.assertEqual(locations[0]["name"], f"{self.prefix} Alpha")

    def test_valid_country_without_locations_returns_empty_list(self):
        used = set(frappe.get_all("AOS Location", pluck="country"))
        country = next((name for name in frappe.get_all("Country", pluck="name") if name not in used), None)
        if not country:
            self.skipTest("Every country has configured locations")
        with patch("aos.api.localization.locations.rate_limit", return_value=None):
            response = get_locations_impl(country=country)
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response["data"]["locations"], [])
