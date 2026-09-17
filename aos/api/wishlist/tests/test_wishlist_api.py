from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from aos.api.wishlist.add import add_to_wishlist_impl
from aos.api.wishlist.list import list_wishlist_impl
from aos.api.wishlist.remove import remove_from_wishlist_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestWishlistAPI(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("wishlist")
        self.created_users: list[str] = []
        frappe.set_user("Administrator")
        self.configure_test_localization_defaults()
        self.seller_user = self.make_user("seller")
        self.buyer_user = self.make_user("buyer")
        self.other_buyer = self.make_user("other-buyer")
        self.ad = self.make_ad(seller_user=self.seller_user, status="Active")
        frappe.set_user(self.buyer_user)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _add(self, **kwargs):
        with (
            patch("aos.api.wishlist.mutation.rate_limit", return_value=None),
            patch("aos.api.wishlist.mutation._schedule_activity"),
            patch("aos.services.wishlist.counters._enqueue_search_refresh"),
        ):
            return add_to_wishlist_impl(**kwargs)

    def _remove(self, **kwargs):
        with (
            patch("aos.api.wishlist.mutation.rate_limit", return_value=None),
            patch("aos.api.wishlist.mutation._schedule_activity"),
            patch("aos.services.wishlist.counters._enqueue_search_refresh"),
        ):
            return remove_from_wishlist_impl(**kwargs)

    def _list(self, **kwargs):
        with patch("aos.api.wishlist.list.rate_limit", return_value=None):
            return list_wishlist_impl(**kwargs)

    def test_explicit_add_and_remove_are_idempotent_and_keep_exact_count(self):
        added = self._add(ad_id=self.ad.public_id)
        added_again = self._add(ad_id=self.ad.public_id)
        removed = self._remove(ad_id=self.ad.public_id)
        removed_again = self._remove(ad_id=self.ad.public_id)

        self.assertTrue(added.get("ok"), added)
        self.assertTrue(added.get("data", {}).get("wishlisted"))
        self.assertTrue(added.get("data", {}).get("changed"))
        self.assertEqual(added.get("data", {}).get("ad_id"), self.ad.public_id)
        self.assertEqual(added.get("data", {}).get("wishlist_count"), 1)

        self.assertTrue(added_again.get("ok"), added_again)
        self.assertFalse(added_again.get("data", {}).get("changed"))
        self.assertEqual(added_again.get("data", {}).get("wishlist_count"), 1)

        self.assertTrue(removed.get("ok"), removed)
        self.assertFalse(removed.get("data", {}).get("wishlisted"))
        self.assertTrue(removed.get("data", {}).get("changed"))
        self.assertEqual(removed.get("data", {}).get("wishlist_count"), 0)

        self.assertTrue(removed_again.get("ok"), removed_again)
        self.assertFalse(removed_again.get("data", {}).get("changed"))
        self.assertEqual(removed_again.get("data", {}).get("wishlist_count"), 0)

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

    def test_legacy_toggle_shape_and_internal_ad_name_are_rejected(self):
        legacy = self._add(id=self.ad.public_id, wishlisted=1)
        internal_name = self._add(ad_id=self.ad.name)

        self.assertFalse(legacy.get("ok"), legacy)
        self.assertEqual(legacy.get("error"), "INVALID_WISHLIST_REQUEST")
        self.assertFalse(internal_name.get("ok"), internal_name)
        self.assertEqual(internal_name.get("error"), "AD_NOT_FOUND")

    def test_guest_is_rejected_and_cannot_create_relationship(self):
        frappe.set_user("Guest")
        response = self._add(ad_id=self.ad.public_id)

        self.assertFalse(response.get("ok"), response)
        self.assertFalse(frappe.db.exists("AOS Wishlist", {"ad": self.ad.name}))

    def test_owner_isolation_prevents_removing_another_users_relationship(self):
        added = self._add(ad_id=self.ad.public_id)
        self.assertTrue(added.get("ok"), added)

        frappe.set_user(self.other_buyer)
        removed = self._remove(ad_id=self.ad.public_id)

        self.assertTrue(removed.get("ok"), removed)
        self.assertFalse(removed.get("data", {}).get("wishlisted"))
        self.assertFalse(removed.get("data", {}).get("changed"))
        self.assertTrue(
            frappe.db.exists(
                "AOS Wishlist",
                {"user": self.buyer_user, "ad": self.ad.name, "status": "Active"},
            )
        )

    def test_seller_cannot_wishlist_own_ad(self):
        frappe.set_user(self.seller_user)
        response = self._add(ad_id=self.ad.public_id)

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "OWN_AD_WISHLIST_FORBIDDEN")
        self.assertFalse(frappe.db.exists("AOS Wishlist", {"user": self.seller_user, "ad": self.ad.name}))

    def test_unavailable_ad_can_be_removed_but_not_readded(self):
        added = self._add(ad_id=self.ad.public_id)
        self.assertTrue(added.get("ok"), added)
        frappe.db.set_value("AOS Ad", self.ad.name, "status", "Sold", update_modified=False)

        removed = self._remove(ad_id=self.ad.public_id)
        removed_again = self._remove(ad_id=self.ad.public_id)
        readd = self._add(ad_id=self.ad.public_id)

        self.assertTrue(removed.get("ok"), removed)
        self.assertFalse(removed.get("data", {}).get("wishlisted"))
        self.assertTrue(removed_again.get("ok"), removed_again)
        self.assertFalse(removed_again.get("data", {}).get("changed"))
        self.assertFalse(readd.get("ok"), readd)
        self.assertEqual(readd.get("error"), "AD_NOT_FOUND")

    def test_remove_does_not_reveal_hidden_ad_without_owner_relationship(self):
        frappe.db.set_value("AOS Ad", self.ad.name, "status", "Reviewing", update_modified=False)

        response = self._remove(ad_id=self.ad.public_id)

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "AD_NOT_FOUND")

    def test_deleted_ad_is_hidden_but_relationship_can_still_be_removed(self):
        added = self._add(ad_id=self.ad.public_id)
        self.assertTrue(added.get("ok"), added)
        frappe.db.set_value("AOS Ad", self.ad.name, "status", "Deleted", update_modified=False)

        listed = self._list()
        removed = self._remove(ad_id=self.ad.public_id)

        self.assertTrue(listed.get("ok"), listed)
        self.assertEqual(listed.get("data", {}).get("items"), [])
        self.assertTrue(removed.get("ok"), removed)
        self.assertFalse(removed.get("data", {}).get("wishlisted"))
        self.assertTrue(removed.get("data", {}).get("changed"))

    def test_list_defaults_to_recent_saved_order_and_supports_cursor_pagination(self):
        second_ad = self.make_ad(seller_user=self.seller_user, status="Active")
        self.assertTrue(self._add(ad_id=self.ad.public_id).get("ok"))
        self.assertTrue(self._add(ad_id=second_ad.public_id).get("ok"))

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
        self.assertNotIn("next_offset", pagination)

        second_page = self._list(limit=1, cursor=pagination["next_cursor"])
        self.assertTrue(second_page.get("ok"), second_page)
        second_data = second_page.get("data", {})
        self.assertEqual([item.get("id") for item in second_data.get("items", [])], [self.ad.public_id])
        self.assertFalse(second_data.get("pagination", {}).get("has_more"))
        self.assertIsNone(second_data.get("pagination", {}).get("next_cursor"))

    def test_list_rejects_malformed_cursor(self):
        response = self._list(cursor="not-a-valid-cursor")

        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "INVALID_WISHLIST_CURSOR")

    def test_list_searches_only_within_saved_ads_and_filters_server_side(self):
        second_ad = self.make_ad(seller_user=self.seller_user, status="Active")
        frappe.db.set_value("AOS Ad", self.ad.name, "title", "Wishlist Camera Alpha", update_modified=False)
        frappe.db.set_value("AOS Ad", second_ad.name, "title", "Wishlist Laptop Beta", update_modified=False)
        self.assertTrue(self._add(ad_id=self.ad.public_id).get("ok"))
        self.assertTrue(self._add(ad_id=second_ad.public_id).get("ok"))

        response = self._list(q="camera")

        self.assertTrue(response.get("ok"), response)
        self.assertEqual([item.get("id") for item in response["data"]["items"]], [self.ad.public_id])

    def test_fixture_cleanup_removes_renamed_ads_by_immutable_name(self):
        ad_name = self.ad.name
        seller_name = self.ad.seller
        category_name = self.ad.category
        location_name = self.ad.location

        frappe.db.set_value(
            "AOS Ad",
            ad_name,
            "title",
            "Wishlist Camera Alpha",
            update_modified=False,
        )
        self.cleanup_feature_rows()

        self.assertFalse(frappe.db.exists("AOS Ad", ad_name))
        self.assertFalse(frappe.db.exists("AOS Seller", seller_name))
        self.assertFalse(frappe.db.exists("AOS Category", category_name))
        self.assertFalse(frappe.db.exists("AOS Location", location_name))

    def test_list_supports_price_sort_and_price_filter_with_cursor(self):
        second_ad = self.make_ad(seller_user=self.seller_user, status="Active")
        frappe.db.set_value("AOS Ad", self.ad.name, "price", 300, update_modified=False)
        frappe.db.set_value("AOS Ad", second_ad.name, "price", 100, update_modified=False)
        self.assertTrue(self._add(ad_id=self.ad.public_id).get("ok"))
        self.assertTrue(self._add(ad_id=second_ad.public_id).get("ok"))

        first = self._list(sort="price_low", price_min="50", price_max="350", limit=1)
        self.assertTrue(first.get("ok"), first)
        self.assertEqual([item.get("id") for item in first["data"]["items"]], [second_ad.public_id])
        cursor = first["data"]["pagination"]["next_cursor"]
        self.assertTrue(cursor)

        second = self._list(sort="price_low", price_min="50", price_max="350", limit=1, cursor=cursor)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual([item.get("id") for item in second["data"]["items"]], [self.ad.public_id])

    def test_list_cursor_is_bound_to_filter_and_sort_scope(self):
        second_ad = self.make_ad(seller_user=self.seller_user, status="Active")
        self.assertTrue(self._add(ad_id=self.ad.public_id).get("ok"))
        self.assertTrue(self._add(ad_id=second_ad.public_id).get("ok"))
        first = self._list(limit=1, sort="saved_recent")
        cursor = first["data"]["pagination"]["next_cursor"]
        self.assertTrue(cursor)

        changed_sort = self._list(limit=1, sort="saved_oldest", cursor=cursor)
        changed_query = self._list(limit=1, sort="saved_recent", q="phone", cursor=cursor)

        self.assertFalse(changed_sort.get("ok"), changed_sort)
        self.assertEqual(changed_sort.get("error"), "INVALID_WISHLIST_CURSOR")
        self.assertFalse(changed_query.get("ok"), changed_query)
        self.assertEqual(changed_query.get("error"), "INVALID_WISHLIST_CURSOR")

    def test_list_supports_category_location_seller_rating_and_verified_filters(self):
        second_seller_user = self.make_user("seller-two")
        other_ad = self.make_ad(seller_user=second_seller_user, status="Active")
        seller = self.make_seller(self.seller_user)
        profile_name = frappe.db.get_value("AOS Profile", {"user": self.seller_user}, "name")
        frappe.db.set_value("AOS Profile", profile_name, "is_verified", 1, update_modified=False)
        frappe.db.set_value("AOS Ad", self.ad.name, "average_rating", 4.5, update_modified=False)
        frappe.db.set_value("AOS Ad", other_ad.name, "average_rating", 2.0, update_modified=False)
        self.assertTrue(self._add(ad_id=self.ad.public_id).get("ok"))
        self.assertTrue(self._add(ad_id=other_ad.public_id).get("ok"))

        response = self._list(
            category=self.ad.category,
            location=self.ad.location,
            seller=seller.public_id,
            rating_min="4",
            verified_seller=1,
        )

        self.assertTrue(response.get("ok"), response)
        self.assertEqual([item.get("id") for item in response["data"]["items"]], [self.ad.public_id])

    def test_list_omits_unavailable_moderation_hidden_and_blocked_ads(self):
        second_ad = self.make_ad(seller_user=self.seller_user, status="Active")
        self.assertTrue(self._add(ad_id=self.ad.public_id).get("ok"))
        self.assertTrue(self._add(ad_id=second_ad.public_id).get("ok"))
        frappe.db.set_value("AOS Ad", second_ad.name, "status", "Reviewing", update_modified=False)
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

    def test_invalid_ad_returns_canonical_not_found(self):
        response = self._add(ad_id="ad_does-not-exist")
        self.assertFalse(response.get("ok"), response)
        self.assertEqual(response.get("error"), "AD_NOT_FOUND")

    def test_search_ranking_refresh_only_follows_real_state_transitions(self):
        with (
            patch("aos.api.wishlist.mutation.rate_limit", return_value=None),
            patch("aos.api.wishlist.mutation._schedule_activity"),
            patch("aos.services.wishlist.counters._enqueue_search_refresh") as refresh,
        ):
            first_add = add_to_wishlist_impl(ad_id=self.ad.public_id)
            retry_add = add_to_wishlist_impl(ad_id=self.ad.public_id)
            first_remove = remove_from_wishlist_impl(ad_id=self.ad.public_id)
            retry_remove = remove_from_wishlist_impl(ad_id=self.ad.public_id)

        self.assertTrue(first_add.get("data", {}).get("changed"))
        self.assertFalse(retry_add.get("data", {}).get("changed"))
        self.assertTrue(first_remove.get("data", {}).get("changed"))
        self.assertFalse(retry_remove.get("data", {}).get("changed"))
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual(refresh.call_args_list[0].kwargs.get("source"), "wishlist_insert")
        self.assertEqual(refresh.call_args_list[1].kwargs.get("source"), "wishlist_remove")

    def test_search_ranking_enqueue_failure_does_not_corrupt_successful_add(self):
        with (
            patch("aos.api.wishlist.mutation.rate_limit", return_value=None),
            patch("aos.api.wishlist.mutation._schedule_activity"),
            patch(
                "aos.services.search_ranking_service.enqueue_ad_search_index",
                side_effect=RuntimeError("ranking unavailable"),
            ),
        ):
            response = add_to_wishlist_impl(ad_id=self.ad.public_id)

        self.assertTrue(response.get("ok"), response)
        self.assertTrue(
            frappe.db.exists(
                "AOS Wishlist",
                {"user": self.buyer_user, "ad": self.ad.name, "status": "Active"},
            )
        )
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "wishlist_count") or 0), 1)

    def test_activity_scheduling_failure_is_best_effort(self):
        with (
            patch("aos.api.wishlist.mutation.rate_limit", return_value=None),
            patch("aos.services.wishlist.counters._enqueue_search_refresh"),
            patch("aos.api.wishlist.mutation.frappe.enqueue", side_effect=RuntimeError("queue unavailable")),
        ):
            response = add_to_wishlist_impl(ad_id=self.ad.public_id)

        self.assertTrue(response.get("ok"), response)
        self.assertTrue(response.get("data", {}).get("wishlisted"))

    def test_account_purge_removes_relationship_and_recomputes_count(self):
        from aos.services.account_purge_service import _purge_wishlist_batch

        added = self._add(ad_id=self.ad.public_id)
        self.assertTrue(added.get("ok"), added)
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "wishlist_count") or 0), 1)

        with patch("aos.services.wishlist.counters._enqueue_search_refresh"):
            removed = _purge_wishlist_batch(user=self.buyer_user, limit=100)

        self.assertEqual(removed, 1)
        self.assertFalse(frappe.db.exists("AOS Wishlist", {"user": self.buyer_user, "ad": self.ad.name}))
        self.assertEqual(int(frappe.db.get_value("AOS Ad", self.ad.name, "wishlist_count") or 0), 0)

    def test_rate_limit_key_does_not_embed_raw_user_email_and_is_shared_by_add_remove(self):
        captured: list[str] = []

        def capture_rate_limit(**kwargs):
            captured.append(kwargs["key"])
            return None

        with (
            patch("aos.api.wishlist.mutation.rate_limit", side_effect=capture_rate_limit),
            patch("aos.api.wishlist.mutation._schedule_activity"),
            patch("aos.services.wishlist.counters._enqueue_search_refresh"),
        ):
            add_response = add_to_wishlist_impl(ad_id=self.ad.public_id)
            remove_response = remove_from_wishlist_impl(ad_id=self.ad.public_id)

        self.assertTrue(add_response.get("ok"), add_response)
        self.assertTrue(remove_response.get("ok"), remove_response)
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[0], captured[1])
        self.assertNotIn(self.buyer_user.lower(), captured[0])
        self.assertIn("sha256", captured[0])
