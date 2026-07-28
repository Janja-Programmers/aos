from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aos.services.reviews.eligibility import get_review_eligibility, review_key


class TestReviewEligibility(unittest.TestCase):
    def _target_side_effect(self, doctype, name_or_filters, fields=None, as_dict=False):
        if doctype == "AOS Ad":
            return SimpleNamespace(name="AD-1", seller="SELLER-1", status="Active", title="Ad", average_rating=0, total_reviews=0)
        if doctype == "AOS Seller":
            return SimpleNamespace(name="SELLER-1", user="seller@example.com", status="Active")
        if doctype == "AOS Review":
            return None
        return None

    def test_review_key_is_stable_and_pair_specific(self):
        first = review_key(reviewer="buyer@example.com", ad_id="AD-1")
        self.assertEqual(first, review_key(reviewer="buyer@example.com", ad_id="AD-1"))
        self.assertNotEqual(first, review_key(reviewer="buyer@example.com", ad_id="AD-2"))

    @patch("aos.services.reviews.eligibility.has_communication_between_users", return_value=True)
    @patch("aos.services.reviews.eligibility.get_conversation_between_users", return_value="CONV-1")
    @patch("aos.services.reviews.eligibility.is_blocked_between", return_value=False)
    @patch("aos.services.reviews.eligibility.frappe.db.get_value")
    def test_server_derived_communication_grants_eligibility(self, get_value, _blocked, _conversation, _communicated):
        get_value.side_effect = self._target_side_effect
        state = get_review_eligibility(ad_id="AD-1", reviewer="buyer@example.com")
        self.assertTrue(state["can_review"])
        self.assertEqual(state["eligibility_basis"], "communication")
        self.assertEqual(state["_eligibility_reference"], "CONV-1")

    @patch("aos.services.reviews.eligibility.has_communication_between_users", return_value=False)
    @patch("aos.services.reviews.eligibility.get_conversation_between_users", return_value="CONV-1")
    @patch("aos.services.reviews.eligibility.is_blocked_between", return_value=False)
    @patch("aos.services.reviews.eligibility.frappe.db.get_value")
    def test_missing_communication_is_not_eligible(self, get_value, _blocked, _conversation, _communicated):
        get_value.side_effect = self._target_side_effect
        state = get_review_eligibility(ad_id="AD-1", reviewer="buyer@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "TRANSACTION_NOT_ELIGIBLE")

    @patch("aos.services.reviews.eligibility.is_blocked_between", return_value=True)
    @patch("aos.services.reviews.eligibility.frappe.db.get_value")
    def test_blocked_users_are_not_eligible(self, get_value, _blocked):
        get_value.side_effect = self._target_side_effect
        state = get_review_eligibility(ad_id="AD-1", reviewer="buyer@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "USER_BLOCKED")

    @patch("aos.services.reviews.eligibility.frappe.db.get_value")
    def test_self_review_is_rejected(self, get_value):
        get_value.side_effect = self._target_side_effect
        state = get_review_eligibility(ad_id="AD-1", reviewer="seller@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "REVIEW_SELF_NOT_ALLOWED")

    @patch("aos.services.reviews.eligibility.is_blocked_between", return_value=False)
    @patch("aos.services.reviews.eligibility.frappe.db.get_value")
    def test_existing_review_blocks_duplicate(self, get_value, _blocked):
        def values(doctype, name_or_filters, fields=None, as_dict=False):
            if doctype == "AOS Review":
                return SimpleNamespace(name="RVW-1", status="Approved")
            return self._target_side_effect(doctype, name_or_filters, fields, as_dict)
        get_value.side_effect = values
        state = get_review_eligibility(ad_id="AD-1", reviewer="buyer@example.com")
        self.assertFalse(state["can_review"])
        self.assertEqual(state["reason"], "REVIEW_ALREADY_EXISTS")
        self.assertEqual(state["existing_review_id"], "RVW-1")
