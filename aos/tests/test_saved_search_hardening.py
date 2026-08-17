from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.saved_search.constants import MAX_SAVED_SEARCH_PARAMS_BYTES
from aos.api.saved_search.list import list_saved_searches_impl
from aos.api.saved_search.save import save_search_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSavedSearchHardening(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix = self.make_prefix("saved-search")
        self.created_users: list[str] = []
        self.user = self.make_user("owner", with_preference=True)
        frappe.set_user(self.user)

    def tearDown(self):
        self.cleanup_feature_rows()
        frappe.set_user("Administrator")

    def _save(self, *, title: str, query: str):
        with patch("aos.api.saved_search.save.rate_limit", return_value=None):
            return save_search_impl(title=title, params_json={"q": query})

    def test_duplicate_post_reuses_one_user_scoped_search(self):
        first = self._save(title="First", query="phone")
        second = self._save(title="Retried request", query="phone")

        self.assertTrue(first.get("ok"), first)
        self.assertTrue(second.get("ok"), second)
        self.assertEqual(first["data"]["id"], second["data"]["id"])
        self.assertEqual(
            frappe.db.count("AOS Saved Search", {"user": self.user, "is_active": 1}),
            1,
        )

    def test_list_is_bounded_and_stably_paginated(self):
        for index in range(3):
            response = self._save(title=f"Search {index}", query=f"phone-{index}")
            self.assertTrue(response.get("ok"), response)

        with patch("aos.api.saved_search.list.rate_limit", return_value=None):
            first_page = list_saved_searches_impl(limit=2, offset=0)
            second_page = list_saved_searches_impl(limit=2, offset=2)

        self.assertTrue(first_page.get("ok"), first_page)
        self.assertEqual(len(first_page["data"]["items"]), 2)
        self.assertTrue(first_page["data"]["has_more"])
        self.assertEqual(first_page["data"]["next_offset"], 2)
        self.assertEqual(len(second_page["data"]["items"]), 1)
        self.assertFalse(second_page["data"]["has_more"])

        first_ids = {item["name"] for item in first_page["data"]["items"]}
        second_ids = {item["name"] for item in second_page["data"]["items"]}
        self.assertFalse(first_ids & second_ids)

    def test_payload_and_per_user_count_are_bounded(self):
        with patch("aos.api.saved_search.save.rate_limit", return_value=None):
            oversized = save_search_impl(
                title="Too large",
                params_json={"q": "x" * (MAX_SAVED_SEARCH_PARAMS_BYTES + 1)},
            )
        self.assertFalse(oversized.get("ok"), oversized)
        self.assertEqual(oversized.get("error"), "VALIDATION_ERROR")

        with patch("aos.api.saved_search.save.MAX_SAVED_SEARCHES_PER_USER", 1):
            first = self._save(title="One", query="one")
            second = self._save(title="Two", query="two")

        self.assertTrue(first.get("ok"), first)
        self.assertFalse(second.get("ok"), second)
        self.assertEqual(second.get("error"), "SAVED_SEARCH_LIMIT_REACHED")
