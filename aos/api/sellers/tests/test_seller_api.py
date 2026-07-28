from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.sellers.get_my_seller_status import get_my_seller_status_impl
from aos.api.sellers.get_seller import get_seller_impl
from aos.api.sellers.list_sellers import list_sellers_impl
from aos.api.sellers.update_my_seller import update_my_seller_impl
from aos.services.sellers.policy import set_seller_status
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSellerAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("seller-api")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()
        self.owner = self.make_user("owner")
        self.viewer = self.make_user("viewer")
        self.other = self.make_user("other")
        self.seller = self.make_seller(self.owner)
        frappe.set_user(self.viewer)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    @staticmethod
    def _without_rate_limit(path: str):
        return patch(path, return_value=None)

    def test_status_uses_opaque_identity_and_canonical_capabilities(self):
        frappe.set_user(self.owner)
        with self._without_rate_limit("aos.api.sellers.get_my_seller_status.rate_limit"):
            response = get_my_seller_status_impl()

        self.assertTrue(response.get("ok"), response)
        data = response.get("data", {})
        self.assertRegex(data.get("seller_id") or "", r"^SELLER-[A-Z2-7]{20}$")
        self.assertNotEqual(data.get("seller_id"), self.owner)
        self.assertEqual(data.get("status"), "Active")
        self.assertTrue(data.get("can_post_ads"))
        self.assertTrue(data.get("can_update_storefront"))
        self.assertTrue(data.get("can_manage_location"))

    def test_storefront_update_is_versioned_and_identical_retry_is_idempotent(self):
        frappe.set_user(self.owner)
        with self._without_rate_limit("aos.api.sellers.update_my_seller.rate_limit"):
            updated = update_my_seller_impl(
                business_category="  Vehicles  ",
                about_business="  Trusted local seller.  ",
                operating_hours=[
                    {
                        "day_of_week": "Monday",
                        "is_open": True,
                        "open_time": "08:00",
                        "close_time": "17:00",
                    }
                ],
                expected_version=0,
            )
            repeated = update_my_seller_impl(
                business_category="Vehicles",
                about_business="Trusted local seller.",
                operating_hours=[
                    {
                        "day_of_week": "Monday",
                        "is_open": True,
                        "open_time": "08:00",
                        "close_time": "17:00",
                    }
                ],
                expected_version=1,
            )

        self.assertTrue(updated.get("ok"), updated)
        self.assertTrue(updated.get("data", {}).get("changed"))
        self.assertEqual(updated.get("data", {}).get("storefront_version"), 1)
        self.assertTrue(repeated.get("ok"), repeated)
        self.assertFalse(repeated.get("data", {}).get("changed"))
        self.assertEqual(repeated.get("data", {}).get("storefront_version"), 1)

    def test_stale_storefront_version_fails_without_mutation(self):
        frappe.set_user(self.owner)
        with self._without_rate_limit("aos.api.sellers.update_my_seller.rate_limit"):
            first = update_my_seller_impl(business_category="Vehicles", expected_version=0)
            stale = update_my_seller_impl(business_category="Property", expected_version=0)

        self.assertTrue(first.get("ok"), first)
        self.assertFalse(stale.get("ok"), stale)
        self.assertEqual(stale.get("error"), "SELLER_VERSION_CONFLICT")
        self.seller.reload()
        self.assertEqual(self.seller.business_category, "Vehicles")
        self.assertEqual(int(self.seller.storefront_version or 0), 1)

    def test_public_list_and_detail_never_return_internal_seller_name(self):
        with (
            self._without_rate_limit("aos.api.sellers.list_sellers.rate_limit"),
            self._without_rate_limit("aos.api.sellers.get_seller.rate_limit"),
        ):
            listing = list_sellers_impl(limit=20, sort="recommended")
            detail = get_seller_impl(seller_id=self.seller.public_id)

        self.assertTrue(listing.get("ok"), listing)
        item = next(
            entry
            for entry in listing.get("data", {}).get("items", [])
            if entry.get("seller_id") == self.seller.public_id
        )
        self.assertNotEqual(item.get("seller_id"), self.owner)
        self.assertNotIn(self.owner, str(item))

        self.assertTrue(detail.get("ok"), detail)
        data = detail.get("data", {})
        self.assertEqual(data.get("seller_id"), self.seller.public_id)
        self.assertNotIn(self.owner, str(data))
        self.assertNotIn("email", data)
        self.assertNotIn("phone", data)

    def test_suspended_seller_is_hidden_and_capabilities_fail_closed(self):
        frappe.set_user("Administrator")
        set_seller_status(
            self.seller.name,
            status="Suspended",
            reason_code="TEST_SUSPENSION",
            source="seller_test",
            actor="Administrator",
        )
        frappe.set_user(self.viewer)
        with (
            self._without_rate_limit("aos.api.sellers.list_sellers.rate_limit"),
            self._without_rate_limit("aos.api.sellers.get_seller.rate_limit"),
        ):
            listing = list_sellers_impl(limit=20)
            detail = get_seller_impl(seller_id=self.seller.public_id)

        ids = {entry.get("seller_id") for entry in listing.get("data", {}).get("items", [])}
        self.assertNotIn(self.seller.public_id, ids)
        self.assertFalse(detail.get("ok"), detail)
        self.assertEqual(detail.get("error"), "SELLER_NOT_FOUND")

        frappe.set_user(self.owner)
        with self._without_rate_limit("aos.api.sellers.get_my_seller_status.rate_limit"):
            status = get_my_seller_status_impl()
        self.assertTrue(status.get("ok"), status)
        self.assertEqual(status.get("data", {}).get("status"), "Suspended")
        self.assertFalse(status.get("data", {}).get("can_post_ads"))
        self.assertFalse(status.get("data", {}).get("can_update_storefront"))
        self.assertFalse(status.get("data", {}).get("can_manage_location"))

    def test_block_relationship_hides_direct_and_discovery_reads(self):
        frappe.set_user("Administrator")
        frappe.get_doc(
            {
                "doctype": "AOS User Block",
                "blocker_user": self.owner,
                "blocked_user": self.viewer,
                "status": "Active",
            }
        ).insert(ignore_permissions=True)
        frappe.set_user(self.viewer)

        with (
            self._without_rate_limit("aos.api.sellers.list_sellers.rate_limit"),
            self._without_rate_limit("aos.api.sellers.get_seller.rate_limit"),
        ):
            listing = list_sellers_impl(limit=20)
            detail = get_seller_impl(seller_id=self.seller.public_id)

        ids = {entry.get("seller_id") for entry in listing.get("data", {}).get("items", [])}
        self.assertNotIn(self.seller.public_id, ids)
        self.assertFalse(detail.get("ok"), detail)
        self.assertEqual(detail.get("error"), "SELLER_NOT_FOUND")

    def test_cross_user_banner_media_is_rejected_without_replacing_storefront(self):
        media = self.make_media(owner=self.other, purpose="seller_banner")
        frappe.set_user(self.owner)
        with self._without_rate_limit("aos.api.sellers.update_my_seller.rate_limit"):
            response = update_my_seller_impl(
                shop_banner_media=media.name,
                expected_version=0,
            )

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "MEDIA_ACCESS_DENIED")
        self.seller.reload()
        self.assertFalse(self.seller.shop_banner_media)
        self.assertEqual(int(self.seller.storefront_version or 0), 0)

    def test_controller_blocks_direct_storefront_lifecycle_and_metric_mutation(self):
        frappe.set_user(self.owner)

        storefront = frappe.get_doc("AOS Seller", self.seller.name)
        storefront.business_category = "Bypass"
        with self.assertRaises(frappe.PermissionError):
            storefront.save(ignore_permissions=True)

        lifecycle = frappe.get_doc("AOS Seller", self.seller.name)
        lifecycle.status = "Suspended"
        lifecycle.status_reason_code = "BYPASS"
        lifecycle.status_source = "test"
        with self.assertRaises(frappe.PermissionError):
            lifecycle.save(ignore_permissions=True)

        metrics = frappe.get_doc("AOS Seller", self.seller.name)
        metrics.total_ads = 999
        with self.assertRaises(frappe.PermissionError):
            metrics.save(ignore_permissions=True)
