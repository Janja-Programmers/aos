from __future__ import annotations

import json
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import install_wishlist_indexes


class TestWishlistDatabaseContracts(FrappeTestCase):
    def test_wishlist_schema_indexes_are_idempotent_and_exact(self):
        install_wishlist_indexes.execute()
        install_wishlist_indexes.execute()

        for expected_name, expected_columns, expected_unique in install_wishlist_indexes.INDEXES:
            rows = frappe.db.sql(
                """
                SELECT COLUMN_NAME, NON_UNIQUE
                FROM information_schema.STATISTICS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'tabAOS Wishlist'
                  AND INDEX_NAME = %s
                ORDER BY SEQ_IN_INDEX
                """,
                (expected_name,),
                as_dict=True,
            )
            self.assertTrue(rows, expected_name)
            self.assertEqual(tuple(row.COLUMN_NAME for row in rows), expected_columns)
            self.assertEqual(not bool(int(rows[0].NON_UNIQUE)), expected_unique)

    def test_wishlist_schema_exposes_current_lifecycle_fields_and_is_not_web_indexed(self):
        wishlist_meta = frappe.get_meta("AOS Wishlist")
        ad_meta = frappe.get_meta("AOS Ad")

        self.assertEqual(wishlist_meta.get_field("saved_on").fieldtype, "Datetime")
        self.assertEqual(wishlist_meta.get_field("removed_on").fieldtype, "Datetime")
        self.assertTrue(wishlist_meta.get_field("saved_on").read_only)
        self.assertTrue(wishlist_meta.get_field("removed_on").read_only)
        self.assertEqual(ad_meta.get_field("wishlist_count").fieldtype, "Int")
        self.assertTrue(ad_meta.get_field("wishlist_count").read_only)
        schema_file = (
            Path(__file__).resolve().parents[1] / "aos/doctype/aos_wishlist/aos_wishlist.json"
        )
        self.assertEqual(
            json.loads(schema_file.read_text(encoding="utf-8"))["index_web_pages_for_search"],
            0,
        )

    def test_database_rejects_duplicate_user_ad_relationship_even_with_manual_name(self):
        # The pair uniqueness invariant is independent of the deterministic name.
        indexes = {name: (columns, unique) for name, columns, unique in install_wishlist_indexes.INDEXES}
        self.assertEqual(indexes["uq_aos_wishlist_user_ad"], (("user", "ad"), True))
