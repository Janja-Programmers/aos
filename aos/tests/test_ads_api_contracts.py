from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.api.v1 import ads as ads_v1


class TestAdsApiContracts(FrappeTestCase):
    def _source(self, relative: str) -> str:
        return Path(frappe.get_app_path("aos", *relative.split("/"))).read_text(encoding="utf-8")

    def test_mutation_implementations_do_not_commit_outer_transactions(self):
        files = [
            "api/ads/create.py",
            "api/ads/update.py",
            "api/ads/status.py",
            "api/ads/drafts.py",
            "api/wishlist/toggle.py",
            "api/reports/report_ad.py",
            "tasks/ads.py",
        ]
        offenders: list[str] = []
        for relative in files:
            tree = ast.parse(self._source(relative), filename=relative)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "commit"
                ):
                    offenders.append(f"{relative}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_public_detail_does_not_select_raw_ad_documents(self):
        source = self._source("api/ads/get_ad.py")
        self.assertNotIn("a.*", source)
        self.assertIn("a.status = 'Active'", source)
        self.assertIn("s.status = 'Active'", source)
        self.assertIn("AOS User Block", source)

    def test_public_detail_emits_analytics_with_keyword_only_canonical_fields(self):
        source = self._source("api/ads/get_ad.py")
        tree = ast.parse(source, filename="api/ads/get_ad.py")
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "emit_analytics_event"
        ]
        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call.args, [])
        keyword_names = {item.arg for item in call.keywords}
        self.assertTrue(
            {
                "event_type",
                "event_group",
                "user",
                "target_doctype",
                "target_name",
                "route_type",
                "route_id",
                "source",
            }.issubset(keyword_names)
        )

    def test_public_list_is_bounded_deterministic_and_cursor_safe(self):
        source = self._source("api/ads/list_ads.py")
        self.assertIn("LIMIT %(limit)s OFFSET %(offset)s", source)
        self.assertIn("a.creation DESC, a.public_id DESC", source)
        self.assertIn('order_by = f"{geo_bucket} ASC, {primary}"', source)
        self.assertIn("decode_recent_cursor", source)
        self.assertIn("safe_like_contains", source)
        self.assertIn("AOS User Block", source)
        self.assertIn("MAX_IMAGES", source)

    def test_wishlist_and_image_search_recheck_public_eligibility(self):
        wishlist = self._source("api/wishlist/list.py")
        image_search = self._source("api/ads/image_search.py")
        self.assertIn("seller.status = 'Active'", wishlist)
        self.assertIn("expires_on", wishlist)
        self.assertIn("AOS User Block", wishlist)
        self.assertIn("normalize_wishlist_list_filters", wishlist)
        self.assertIn("load_public_ad_items", image_search)
        self.assertNotIn("matched_media_id", image_search)

    def test_ads_apis_use_central_validation_and_lifecycle(self):
        create_source = self._source("api/ads/create.py")
        update_source = self._source("api/ads/update.py")
        status_source = self._source("api/ads/status.py")
        self.assertIn("normalize_full_ad_payload", create_source)
        self.assertIn("normalize_active_update", update_source)
        self.assertIn("transition_for_action", status_source)
        self.assertNotIn("float(", create_source)
        self.assertNotIn("float(", update_source)

    def test_v1_ads_transport_strips_cmd_before_strict_validation(self):
        response = {"ok": True}
        with patch.object(ads_v1, "list_ads_impl", return_value=response) as implementation:
            result = ads_v1.list_ads(
                cmd="aos.api.v1.ads.list_ads",
                limit="20",
                unexpected="must-remain-visible-to-validator",
            )

        self.assertIs(result, response)
        implementation.assert_called_once_with(
            limit="20",
            unexpected="must-remain-visible-to-validator",
        )
