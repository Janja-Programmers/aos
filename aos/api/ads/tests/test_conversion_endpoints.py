from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from aos.api.ads.get_ad import get_ad_impl
from aos.api.ads.list_ads import list_ads_impl
from aos.api.wishlist.list import list_wishlist_impl
from aos.services.fx_service import FXSnapshot
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestConversionEndpoints(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("conversion-endpoints")
        self.created_users: list[str] = []
        self.rate_backups: dict[str, dict | None] = {}
        self.wishlist_names: list[str] = []
        self.seller_user = self.make_user("seller")
        self.buyer_user = self.make_user("buyer")
        self.ad = self.make_ad(seller_user=self.seller_user)
        filters = {"enabled": 1} if frappe.get_meta("Currency").has_field("enabled") else {}
        self.currencies = frappe.get_all("Currency", filters=filters, pluck="name", limit=3)
        if len(self.currencies) < 3:
            self.skipTest("Three enabled currencies are required")
        self.source, self.base, self.target = self.currencies[:3]
        self.country = self.ad.country

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in self.wishlist_names:
            if frappe.db.exists("AOS Wishlist", name):
                frappe.delete_doc("AOS Wishlist", name, force=True, ignore_permissions=True)
        for currency, backup in self.rate_backups.items():
            if frappe.db.exists("AOS Exchange Rate", currency):
                frappe.delete_doc("AOS Exchange Rate", currency, force=True, ignore_permissions=True)
            if backup:
                frappe.get_doc({"doctype": "AOS Exchange Rate", **backup}).insert(ignore_permissions=True)
        self.cleanup_feature_rows()

    def _remember_rate(self, currency: str):
        if currency not in self.rate_backups:
            self.rate_backups[currency] = frappe.db.get_value(
                "AOS Exchange Rate",
                currency,
                ["currency", "rate_vs_base", "base_currency", "rate_version", "provider_timestamp", "last_updated"],
                as_dict=True,
            )

    def _set_rate(self, currency: str, rate: float | None):
        self._remember_rate(currency)
        if frappe.db.exists("AOS Exchange Rate", currency):
            frappe.delete_doc("AOS Exchange Rate", currency, force=True, ignore_permissions=True)
        if rate is not None:
            now = now_datetime()
            frappe.get_doc(
                {
                    "doctype": "AOS Exchange Rate",
                    "currency": currency,
                    "rate_vs_base": rate,
                    "base_currency": self.base,
                    "rate_version": f"test-{self.prefix}",
                    "provider_timestamp": now,
                    "last_updated": now,
                }
            ).insert(ignore_permissions=True)

    def _patches(self, module: str, display_currency: str):
        settings = SimpleNamespace(
            base_currency=self.base,
            flash_sale_window_days=7,
            fx_max_stale_hours=24,
        )
        if module == "aos.api.ads.get_ad":
            fx_boundary = patch(
                f"{module}.get_fx_snapshot",
                return_value=FXSnapshot(
                    base_currency=self.base,
                    rate_version=f"test-{self.prefix}",
                    as_of=now_datetime(),
                    rates={self.base: 1.0},
                    fresh=True,
                ),
            )
        elif module == "aos.api.wishlist.list":
            # Wishlist deliberately delegates Ad pricing/projection to the
            # canonical Marketplace Discovery projection. Patch the owner of
            # the FX settings rather than reintroducing an unused compatibility
            # import into Wishlist merely for this endpoint regression test.
            fx_boundary = patch(
                "aos.services.marketplace_discovery.projection.get_aos_settings_snapshot",
                return_value=settings,
            )
        else:
            fx_boundary = patch(f"{module}.get_aos_settings_snapshot", return_value=settings)
        return (
            patch(f"{module}.rate_limit", return_value=None),
            patch(f"{module}.resolve_market_context", return_value=(self.country, display_currency, None)),
            fx_boundary,
        )

    def test_list_missing_source_rate_keeps_original_number_and_currency(self):
        frappe.db.set_value("AOS Ad", self.ad.name, {"currency": self.source, "price": 100})
        self._set_rate(self.source, None)
        with self._patches("aos.api.ads.list_ads", self.base)[0], self._patches("aos.api.ads.list_ads", self.base)[1], self._patches("aos.api.ads.list_ads", self.base)[2]:
            response = list_ads_impl(limit=50)
        item = next(row for row in response["data"]["items"] if row["id"] == self.ad.public_id)
        self.assertEqual((item["display_price"], item["display_currency"]), (100, self.source))
        self.assertFalse(item["price_conversion"]["available"])

    def test_get_ad_missing_target_rate_keeps_original_number_and_currency(self):
        frappe.db.set_value("AOS Ad", self.ad.name, {"currency": self.base, "price": 100})
        self._set_rate(self.target, None)
        with self._patches("aos.api.ads.get_ad", self.target)[0], self._patches("aos.api.ads.get_ad", self.target)[1], self._patches("aos.api.ads.get_ad", self.target)[2]:
            response = get_ad_impl(ad_id=self.ad.public_id)
        item = response["data"]["item"]
        self.assertEqual((item["display_price"], item["display_currency"]), (100, self.base))
        self.assertFalse(item["price_conversion"]["available"])

    def test_wishlist_missing_rate_keeps_original_number_and_currency(self):
        frappe.db.set_value("AOS Ad", self.ad.name, {"currency": self.source, "price": 100})
        self._set_rate(self.source, None)
        wish = frappe.get_doc({"doctype": "AOS Wishlist", "user": self.buyer_user, "ad": self.ad.name, "status": "Active"}).insert(ignore_permissions=True)
        self.wishlist_names.append(wish.name)
        frappe.set_user(self.buyer_user)
        with self._patches("aos.api.wishlist.list", self.base)[0], self._patches("aos.api.wishlist.list", self.base)[1], self._patches("aos.api.wishlist.list", self.base)[2]:
            response = list_wishlist_impl(limit=50)
        item = next(row for row in response["data"]["items"] if row["id"] == self.ad.public_id)
        self.assertEqual((item["display_price"], item["display_currency"]), (100, self.source))
        self.assertFalse(item["price_conversion"]["available"])

    def test_list_with_valid_rates_converts_number_and_metadata(self):
        frappe.db.set_value("AOS Ad", self.ad.name, {"currency": self.source, "price": 100})
        self._set_rate(self.source, 2)
        self._set_rate(self.target, 4)
        with self._patches("aos.api.ads.list_ads", self.target)[0], self._patches("aos.api.ads.list_ads", self.target)[1], self._patches("aos.api.ads.list_ads", self.target)[2]:
            response = list_ads_impl(limit=50)
        item = next(row for row in response["data"]["items"] if row["id"] == self.ad.public_id)
        self.assertEqual((item["display_price"], item["display_currency"]), (200, self.target))
        self.assertTrue(item["price_conversion"]["available"])
        self.assertEqual(item["price_conversion"]["rate"], 2)
