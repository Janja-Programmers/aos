from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.maps.seller_locations import (
    get_seller_location_impl,
    remove_my_seller_location_impl,
    set_my_seller_location_impl,
)
from aos.services.sellers.policy import set_seller_status
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSellerLocationAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("maps-seller")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()
        self.owner = self.make_user("owner")
        self.viewer = self.make_user("viewer")
        self.seller = self.make_seller(self.owner)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    @staticmethod
    def _resolved_location():
        return {
            "display_address": "Kenyatta Avenue, Nairobi, Kenya",
            "locality": "Nairobi",
            "region": "Nairobi County",
            "country_code": "KE",
        }

    def test_location_update_is_versioned_and_identical_retry_is_idempotent(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.maps.seller_locations.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            first = set_my_seller_location_impl(
                latitude=-1.286389,
                longitude=36.817223,
                location_name="Main Shop",
                expected_version=0,
            )
            repeated = set_my_seller_location_impl(
                latitude=-1.286389,
                longitude=36.817223,
                location_name="Main Shop",
                expected_version=0,
            )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(first.get("data", {}).get("changed"))
        self.assertEqual(first.get("data", {}).get("location_version"), 1)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertFalse(repeated.get("data", {}).get("changed"))
        self.assertEqual(repeated.get("data", {}).get("location_version"), 1)

    def test_stale_location_version_fails_without_mutation(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.maps.seller_locations.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            first = set_my_seller_location_impl(
                latitude=-1.286389,
                longitude=36.817223,
                expected_version=0,
            )
            stale = set_my_seller_location_impl(
                latitude=-4.043477,
                longitude=39.668205,
                expected_version=0,
            )

        self.assertTrue(first.get("ok"), first)
        self.assertFalse(stale.get("ok"), stale)
        self.assertEqual(stale.get("error"), "MAP_LOCATION_VERSION_CONFLICT")
        self.seller.reload()
        self.assertAlmostEqual(float(self.seller.latitude), -1.286389, places=6)
        self.assertEqual(int(self.seller.location_version or 0), 1)

    def test_remove_is_versioned_and_idempotent(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.maps.seller_locations.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            set_response = set_my_seller_location_impl(
                latitude=-1.286389,
                longitude=36.817223,
                expected_version=0,
            )
            removed = remove_my_seller_location_impl(expected_version=1)
            repeated = remove_my_seller_location_impl(expected_version=2)

        self.assertTrue(set_response.get("ok"), set_response)
        self.assertTrue(removed.get("ok"), removed)
        self.assertTrue(removed.get("data", {}).get("changed"))
        self.assertEqual(removed.get("data", {}).get("location_version"), 2)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertFalse(repeated.get("data", {}).get("changed"))

    def test_public_location_is_hidden_after_suspension(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.maps.seller_locations.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            created = set_my_seller_location_impl(
                latitude=-1.286389,
                longitude=36.817223,
                expected_version=0,
            )
        self.assertTrue(created.get("ok"), created)

        frappe.set_user("Administrator")
        set_seller_status(
            self.seller.name,
            status="Suspended",
            reason_code="TEST_SUSPENSION",
            source="maps_test",
            actor="Administrator",
        )
        frappe.set_user(self.viewer)
        with patch("aos.api.maps.seller_locations.rate_limit", return_value=None):
            response = get_seller_location_impl(seller_id=self.seller.public_id)
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "MAP_LOCATION_NOT_FOUND")
