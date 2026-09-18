from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aos.services.reviews.eligibility import get_review_eligibility, review_key
from aos.services.reviews.errors import ReviewNotFoundError


class TestReviewEligibility(unittest.TestCase):
    def setUp(self):
        self.target = {
            "ad": SimpleNamespace(name="AD-INTERNAL", public_id="ad_public", seller="SELLER-1"),
            "seller": SimpleNamespace(name="SELLER-1", public_id="SELLER-PUBLIC", user="seller@example.com", status="Active"),
        }

    def test_review_key_is_stable_and_pair_specific(self):
        first = review_key(reviewer="buyer@example.com", ad_id="AD-1")
        self.assertEqual(first, review_key(reviewer="buyer@example.com", ad_id="AD-1"))
        self.assertNotEqual(first, review_key(reviewer="buyer@example.com", ad_id="AD-2"))

    @patch("aos.services.reviews.eligibility.has_communication_between_users", return_value=True)
    @patch("aos.services.reviews.eligibility.get_conversation_between_users", return_value="CONV-1")
    @patch("aos.services.reviews.eligibility.frappe.db.get_value", return_value=None)
    @patch("aos.services.reviews.eligibility.resolve_ad_target")
    def test_server_derived_communication_grants_eligibility(self, target, _review, _conversation, _communicated):
        target.return_value = self.target
        state = get_review_eligibility(ad_id="ad_public", reviewer="buyer@example.com")
        self.assertTrue(state["can_review"])
        self.assertEqual(state["eligibility_basis"], "communication")
        self.assertEqual(state["_eligibility_reference"], "CONV-1")
        self.assertEqual(state["_ad_name"], "AD-INTERNAL")

    @patch("aos.services.reviews.eligibility.has_communication_between_users", return_value=False)
    @patch("aos.services.reviews.eligibility.get_conversation_between_users", return_value="CONV-1")
    @patch("aos.services.reviews.eligibility.frappe.db.get_value", return_value=None)
    @patch("aos.services.reviews.eligibility.resolve_ad_target")
    def test_missing_communication_is_not_eligible(self, target, _review, _conversation, _communicated):
        target.return_value = self.target
        state = get_review_eligibility(ad_id="ad_public", reviewer="buyer@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "TRANSACTION_NOT_ELIGIBLE")

    @patch("aos.services.reviews.eligibility.resolve_ad_target")
    def test_hidden_parent_is_not_enumerated(self, target):
        target.side_effect = ReviewNotFoundError("not found")
        state = get_review_eligibility(ad_id="ad_hidden", reviewer="buyer@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "AD_NOT_FOUND")

    @patch("aos.services.reviews.eligibility.frappe.db.get_value")
    @patch("aos.services.reviews.eligibility.resolve_ad_target")
    def test_existing_review_blocks_duplicate_using_public_review_id(self, target, get_value):
        target.return_value = self.target
        get_value.return_value = SimpleNamespace(public_id="review_public", status="Approved")
        state = get_review_eligibility(ad_id="ad_public", reviewer="buyer@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "REVIEW_ALREADY_EXISTS")
        self.assertEqual(state["existing_review_id"], "review_public")

    @patch("aos.services.reviews.eligibility.resolve_ad_target")
    def test_self_review_is_rejected(self, target):
        target.return_value = self.target
        state = get_review_eligibility(ad_id="ad_public", reviewer="seller@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "REVIEW_SELF_NOT_ALLOWED")
