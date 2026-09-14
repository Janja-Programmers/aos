from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.sellers.get_location import get_seller_location_impl
from aos.api.sellers.remove_location import remove_my_seller_location_impl
from aos.api.sellers.set_location import set_my_seller_location_impl
from aos.services.maps.service import MapsService
from aos.services.sellers.policy import set_seller_status
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSellerLocationAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("seller-location")
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
            "display_address": "George Street, Sydney NSW, Australia",
            "locality": "Sydney",
            "region": "New South Wales",
            "country_code": "AU",
        }

    def test_worldwide_location_is_versioned_and_identical_retry_is_idempotent(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.sellers.set_location.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()) as reverse,
        ):
            first = set_my_seller_location_impl(
                latitude=-33.8688, longitude=151.2093, location_name="Main Shop", expected_version=0
            )
            repeated = set_my_seller_location_impl(
                latitude=-33.8688, longitude=151.2093, location_name="Main Shop", expected_version=0
            )

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(first.get("data", {}).get("changed"))
        self.assertEqual(first.get("data", {}).get("location_version"), 1)
        self.assertEqual(first.get("data", {}).get("location", {}).get("country_code"), "AU")
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertFalse(repeated.get("data", {}).get("changed"))
        self.assertEqual(repeated.get("data", {}).get("location_version"), 1)
        self.assertEqual(reverse.call_count, 1)

    def test_antimeridian_and_high_latitude_coordinates_are_not_region_restricted(self):
        frappe.set_user(self.owner)
        resolved = {
            "display_address": "Storefront destination",
            "locality": "Test Locality",
            "region": "Test Region",
            "country_code": "US",
        }
        with (
            patch("aos.api.sellers.set_location.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=resolved),
        ):
            response = set_my_seller_location_impl(latitude=71.2906, longitude=-179.9, expected_version=0)
        self.assertTrue(response.get("ok"), response)
        self.seller.reload()
        self.assertAlmostEqual(float(self.seller.latitude), 71.2906, places=6)
        self.assertAlmostEqual(float(self.seller.longitude), -179.9, places=6)

    def test_stale_location_version_fails_without_mutation(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.sellers.set_location.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            first = set_my_seller_location_impl(latitude=-33.8688, longitude=151.2093, expected_version=0)
            stale = set_my_seller_location_impl(latitude=35.6762, longitude=139.6503, expected_version=0)
        self.assertTrue(first.get("ok"), first)
        self.assertFalse(stale.get("ok"), stale)
        self.assertEqual(stale.get("error"), "SELLER_LOCATION_VERSION_CONFLICT")
        self.seller.reload()
        self.assertAlmostEqual(float(self.seller.latitude), -33.8688, places=6)
        self.assertEqual(int(self.seller.location_version or 0), 1)

    def test_remove_is_versioned_and_idempotent(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.sellers.set_location.rate_limit", return_value=None),
            patch("aos.api.sellers.remove_location.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            created = set_my_seller_location_impl(latitude=-33.8688, longitude=151.2093, expected_version=0)
            removed = remove_my_seller_location_impl(expected_version=1)
            repeated = remove_my_seller_location_impl(expected_version=2)
        self.assertTrue(created.get("ok"), created)
        self.assertTrue(removed.get("ok"), removed)
        self.assertTrue(removed.get("data", {}).get("changed"))
        self.assertEqual(removed.get("data", {}).get("location_version"), 2)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertFalse(repeated.get("data", {}).get("changed"))

    def test_public_location_hides_owner_concurrency_metadata(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.sellers.set_location.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            self.assertTrue(set_my_seller_location_impl(latitude=-33.8688, longitude=151.2093, expected_version=0).get("ok"))
        frappe.set_user(self.viewer)
        with patch("aos.api.sellers.get_location.rate_limit", return_value=None):
            response = get_seller_location_impl(seller_id=self.seller.public_id)
        self.assertTrue(response.get("ok"), response)
        data = response.get("data", {})
        self.assertFalse(data.get("is_owner"))
        self.assertNotIn("location_version", data)
        self.assertNotIn("version", data.get("location", {}))

    def test_suspended_seller_location_is_hidden_and_route_lookup_fails_closed(self):
        frappe.set_user(self.owner)
        with (
            patch("aos.api.sellers.set_location.rate_limit", return_value=None),
            patch("aos.services.maps.service.MapsService.resolve_location", return_value=self._resolved_location()),
        ):
            self.assertTrue(set_my_seller_location_impl(latitude=-33.8688, longitude=151.2093, expected_version=0).get("ok"))
        frappe.set_user("Administrator")
        set_seller_status(self.seller.name, status="Suspended", reason_code="TEST_SUSPENSION", source="seller_test", actor="Administrator")
        frappe.set_user(self.viewer)
        with patch("aos.api.sellers.get_location.rate_limit", return_value=None):
            response = get_seller_location_impl(seller_id=self.seller.public_id)
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "SELLER_NOT_FOUND")
        with self.assertRaises(Exception) as context:
            MapsService().route(
                {
                    "locations": [{"latitude": -33.87, "longitude": 151.20}],
                    "destination_seller": self.seller.public_id,
                    "costing": "auto", "units": "kilometers", "language": "en",
                },
                viewer=self.viewer,
            )
        self.assertEqual(getattr(context.exception, "code", None), "MAP_LOCATION_NOT_FOUND")
