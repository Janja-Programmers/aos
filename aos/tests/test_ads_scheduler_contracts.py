from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from aos.tasks.ads import expire_ads
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestAdsSchedulerContracts(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("ads-expiry")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()

    def tearDown(self):
        self.cleanup_feature_rows()
        self.restore_localization_test_state()
        frappe.set_user("Administrator")

    def test_expiry_is_not_blocked_by_current_seller_currency_preference(self):
        user = self.make_user("seller")
        ad = self.make_ad(seller_user=user, status="Active")
        original_currency = frappe.db.get_value("AOS User Preference", {"user": user}, "currency")
        other = frappe.get_all(
            "Currency",
            filters={"name": ["!=", original_currency]},
            pluck="name",
            order_by="name asc",
            limit=1,
        )
        self.assertTrue(other)
        other_currency = other[0]

        frappe.db.set_value(
            "AOS User Preference", {"user": user}, "currency", other_currency, update_modified=False
        )
        frappe.db.set_value(
            "AOS Ad", ad.name, "expires_on", add_days(today(), -1), update_modified=False
        )

        with (
            patch("aos.tasks.ads.NotificationService.notify_ad_expired"),
            patch("aos.tasks.ads.enqueue_discovery_refresh"),
        ):
            expire_ads()

        self.assertEqual(frappe.db.get_value("AOS Ad", ad.name, "status"), "Expired")
