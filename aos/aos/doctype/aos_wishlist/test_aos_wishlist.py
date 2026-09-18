from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase

from .aos_wishlist import wishlist_name


class IntegrationTestAOSWishlist(IntegrationTestCase):
    def test_deterministic_name_is_stable_and_pair_specific(self):
        first = wishlist_name("buyer@example.com", "AD-00000000000000000000000000000001")
        repeated = wishlist_name("buyer@example.com", "AD-00000000000000000000000000000001")
        other = wishlist_name("buyer@example.com", "AD-00000000000000000000000000000002")

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("WISH-"))
        self.assertEqual(len(first), 37)

    def test_lifecycle_fields_are_read_only(self):
        meta = frappe.get_meta("AOS Wishlist")

        for fieldname in ("user", "ad", "status", "saved_on", "removed_on"):
            self.assertTrue(meta.get_field(fieldname).read_only, fieldname)
