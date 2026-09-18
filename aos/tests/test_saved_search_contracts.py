from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.saved_search.constants import MAX_SAVED_SEARCH_PARAMS_BYTES
from aos.api.saved_search.create import create_saved_search_impl
from aos.api.saved_search.list import list_saved_searches_impl
from aos.tests.feature_test_helpers import AOSFeatureTestMixin


class TestSavedSearchContracts(AOSFeatureTestMixin, FrappeTestCase):
    def setUp(self):
        self.prefix=self.make_prefix("saved-search"); self.created_users=[]
        self.user=self.make_user("owner",with_preference=True); frappe.set_user(self.user)

    def tearDown(self):
        self.cleanup_feature_rows(); frappe.set_user("Administrator")

    def _save(self, *, title:str, query:str):
        with patch("aos.api.saved_search.create.rate_limit",return_value=None):
            return create_saved_search_impl(title=title,query={"q":query})

    def test_duplicate_canonical_query_is_a_conflict(self):
        first=self._save(title="First",query="phone"); second=self._save(title="Retry",query="phone")
        self.assertTrue(first.get("ok"),first); self.assertFalse(second.get("ok"),second)
        self.assertEqual(second.get("error"),"SAVED_SEARCH_CONFLICT")
        self.assertEqual(frappe.db.count("AOS Saved Search",{"user":self.user,"is_active":1}),1)

    def test_list_is_bounded_and_cursor_paginated(self):
        for i in range(3): self.assertTrue(self._save(title=f"Search {i}",query=f"phone-{i}").get("ok"))
        with patch("aos.api.saved_search.list.rate_limit",return_value=None):
            first=list_saved_searches_impl(limit=2); second=list_saved_searches_impl(limit=2,cursor=first["data"]["next_cursor"])
        self.assertEqual(len(first["data"]["items"]),2); self.assertIsNotNone(first["data"]["next_cursor"])
        self.assertEqual(len(second["data"]["items"]),1)
        self.assertFalse({x["id"] for x in first["data"]["items"]}&{x["id"] for x in second["data"]["items"]})

    def test_payload_and_count_are_bounded(self):
        with patch("aos.api.saved_search.create.rate_limit",return_value=None):
            oversized=create_saved_search_impl(title="Too large",query={"q":"x"*(MAX_SAVED_SEARCH_PARAMS_BYTES+1)})
        self.assertFalse(oversized.get("ok"),oversized)
        with patch("aos.api.saved_search.service.MAX_SAVED_SEARCHES_PER_USER",1):
            first=self._save(title="One",query="one"); second=self._save(title="Two",query="two")
        self.assertTrue(first.get("ok")); self.assertFalse(second.get("ok")); self.assertEqual(second.get("error"),"SAVED_SEARCH_LIMIT_REACHED")
