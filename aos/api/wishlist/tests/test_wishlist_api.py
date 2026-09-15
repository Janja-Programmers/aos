from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from aos.api.wishlist.list import list_wishlist_impl
from aos.api.wishlist.toggle import toggle_wishlist_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestWishlistAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("wishlist")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()
        self.seller_user = self.make_user("seller")
        self.buyer_user = self.make_user("buyer")
        self.ad = self.make_ad(seller_user=self.seller_user, status="Active")
        frappe.set_user(self.buyer_user)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _toggle(self, **kwargs):
        with (
            patch("aos.api.wishlist.toggle.rate_limit", return_value=None),
            patch("aos.api.wishlist.toggle.record_ad_wishlist_activity"),
            patch("aos.api.wishlist.toggle.hide_ad_wishlist_activity"),
            patch("aos.services.wishlist.counters._enqueue_search_refresh"),
        ):
            return toggle_wishlist_impl(**kwargs)

    def _list(self, **kwargs):
        with patch("aos.api.wishlist.list.rate_limit", return_value=None):
            return list_wishlist_impl(**kwargs)

    def test_explicit_add_and_remove_are_idempotent_and_keep_exact_count(self):
        added = self._toggle(ad_id=self.ad.name, wishlisted=1)
        added_again = self._toggle(ad_id=self.ad.name, wishlisted=1)
        removed = self._toggle(ad_id=self.ad.name, wishlisted=0)
        removed_again = self._toggle(ad_id=self.ad.name, wishlisted=0)

        self.assertTrue(added.get("ok"), added)
        self.assertTrue(added.get("data", {}).get("wishlisted"))
        self.assertTrue(added.get("data", {}).get("changed"))
        self.assertEqual(added.get("data", {}).get("wishlist_count"), 1)

        self.assertTrue(added_again.get("ok"), added_again)
        self.assertFalse(added_again.get("data", {}).get("changed"))
        self.assertEqual(added_again.get("data", {}).get("wishlist_count"), 1)

        self.assertTrue(removed.get("ok"), removed)
        self.assertFalse(removed.get("data", {}).get("wishlisted"))
        self.assertTrue(removed.get("data", {}).get("changed"))
        self.assertIsNone(removed.get("data", {}).get("wishlist_count"))

        self.assertTrue(removed_again.get("ok"), removed_again)
        self.assertFalse(removed_again.get("data", {}).get("changed"))
        self.assertIsNone(removed_again.get("data", {}).get("wishlist_count"))

        row = frappe.db.get_value(
            "AOS Wishlist",
            {"user": self.buyer_user, "ad": self.ad.name},
            ["status", "saved_on", "removed_on"],
            as_dict=True,
        )
        self.assertEqual(row.status, "Removed")
        self.assertTrue(row.saved_on)
        self.assertTrue(row.removed_on)
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "wishlist_count") or 0), 0)

    def test_legacy_toggle_preserves_sequential_toggle_semantics(self):
        first = self._toggle(id=self.ad.name)
        second = self._toggle(id=self.ad.name)

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(first.get("data", {}).get("wishlisted"))
        self.assertTrue(second.get("ok"), second)
        self.assertFalse(second.get("data", {}).get("wishlisted"))

    def test_seller_cannot_wishlist_own_ad(self):
        frappe.set_user(self.seller_user)

        response = self._toggle(ad_id=self.ad.name, wishlisted=1)

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "OWN_AD_WISHLIST_FORBIDDEN")
        self.assertFalse(frappe.db.exists("AOS Wishlist", {"user": self.seller_user, "ad": self.ad.name}))

    def test_stale_unavailable_ad_can_still_be_removed_by_owner(self):
        added = self._toggle(ad_id=self.ad.name, wishlisted=1)
        self.assertTrue(added.get("ok"), added)
        frappe.db.set_value("AOS Ad", self.ad.name, "status", "Sold", update_modified=False)

        removed = self._toggle(ad_id=self.ad.name, wishlisted=0)
        readd = self._toggle(ad_id=self.ad.name, wishlisted=1)

        self.assertTrue(removed.get("ok"), removed)
        self.assertFalse(removed.get("data", {}).get("wishlisted"))
        self.assertFalse(readd.get("ok"), readd)
        self.assertEqual(readd.get("error"), "AD_NOT_FOUND")

    def test_list_defaults_to_recent_saved_order_and_supports_cursor_pagination(self):
        second_ad = self.make_ad(seller_user=self.seller_user, status="Active")
        first_added = self._toggle(ad_id=self.ad.name, wishlisted=1)
        second_added = self._toggle(ad_id=second_ad.name, wishlisted=1)
        self.assertTrue(first_added.get("ok"), first_added)
        self.assertTrue(second_added.get("ok"), second_added)

        older = now_datetime() - timedelta(minutes=2)
        newer = now_datetime() - timedelta(minutes=1)
        frappe.db.set_value(
            "AOS Wishlist",
            {"user": self.buyer_user, "ad": self.ad.name},
            "saved_on",
            older,
            update_modified=False,
        )
        frappe.db.set_value(
            "AOS Wishlist",
            {"user": self.buyer_user, "ad": second_ad.name},
            "saved_on",
            newer,
            update_modified=False,
        )

        first_page = self._list(limit=1)
        self.assertTrue(first_page.get("ok"), first_page)
        first_data = first_page.get("data", {})
        self.assertEqual([item.get("id") for item in first_data.get("items", [])], [second_ad.public_id])
        pagination = first_data.get("pagination", {})
        self.assertTrue(pagination.get("has_more"))
        self.assertTrue(pagination.get("next_cursor"))
        self.assertEqual(pagination.get("next_offset"), 1)

        second_page = self._list(limit=1, cursor=pagination["next_cursor"])
        self.assertTrue(second_page.get("ok"), second_page)
        second_data = second_page.get("data", {})
        self.assertEqual([item.get("id") for item in second_data.get("items", [])], [self.ad.public_id])
        self.assertFalse(second_data.get("pagination", {}).get("has_more"))
        self.assertIsNone(second_data.get("pagination", {}).get("next_cursor"))

    def test_list_omits_unavailable_and_blocked_ads_without_exposing_reason(self):
        added = self._toggle(ad_id=self.ad.name, wishlisted=1)
        self.assertTrue(added.get("ok"), added)
        frappe.get_doc(
            {
                "doctype": "AOS User Block",
                "blocker_user": self.seller_user,
                "blocked_user": self.buyer_user,
                "status": "Active",
            }
        ).insert(ignore_permissions=True)

        response = self._list()

        self.assertTrue(response.get("ok"), response)
        self.assertEqual(response.get("data", {}).get("items"), [])

    def test_rate_limit_key_does_not_embed_raw_user_email(self):
        captured: dict[str, str] = {}

        def capture_rate_limit(**kwargs):
            captured["key"] = kwargs["key"]
            return None

        with (
            patch("aos.api.wishlist.toggle.rate_limit", side_effect=capture_rate_limit),
            patch("aos.api.wishlist.toggle.record_ad_wishlist_activity"),
            patch("aos.services.wishlist.counters._enqueue_search_refresh"),
        ):
            response = toggle_wishlist_impl(ad_id=self.ad.name, wishlisted=1)

        self.assertTrue(response.get("ok"), response)
        self.assertNotIn(self.buyer_user.lower(), captured["key"])
        self.assertIn("sha256", captured["key"])
