from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import harden_wishlist_subsystem


class TestWishlistDatabaseContracts(FrappeTestCase):
    def test_wishlist_patch_is_idempotent_and_saved_order_index_exists(self):
        harden_wishlist_subsystem.execute()
        harden_wishlist_subsystem.execute()

        rows = frappe.db.sql(
            """
            SELECT INDEX_NAME
            FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'tabAOS Wishlist'
              AND INDEX_NAME = 'idx_aos_wishlist_user_status_saved'
            """
        )
        self.assertTrue(rows)

    def test_wishlist_and_ad_schema_expose_hardened_lifecycle_fields(self):
        wishlist_meta = frappe.get_meta("AOS Wishlist")
        ad_meta = frappe.get_meta("AOS Ad")

        self.assertEqual(wishlist_meta.get_field("saved_on").fieldtype, "Datetime")
        self.assertEqual(wishlist_meta.get_field("removed_on").fieldtype, "Datetime")
        self.assertTrue(wishlist_meta.get_field("saved_on").read_only)
        self.assertTrue(wishlist_meta.get_field("removed_on").read_only)
        self.assertEqual(ad_meta.get_field("wishlist_count").fieldtype, "Int")
        self.assertTrue(ad_meta.get_field("wishlist_count").read_only)
