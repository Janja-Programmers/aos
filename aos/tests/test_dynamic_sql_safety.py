from __future__ import annotations

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

import importlib

ads_list = importlib.import_module("aos.api.ads.list_ads")
from aos.api.shared.sql_safety import (
    clean_safe_docnames,
    require_dotted_sql_identifier,
    safe_like_contains,
    safe_like_prefix,
)
from aos.api.shorts import feed as shorts_feed
from aos.api.shorts import library as shorts_library
from aos.api.shorts import sounds as shorts_sounds
from aos.api.shorts.utils import (
    build_cursor_where_clause,
    build_ranked_cursor_where_clause,
    build_time_id_cursor,
    encode_cursor,
)
list_sellers = importlib.import_module("aos.api.sellers.list_sellers")
seller_service = importlib.import_module("aos.services.sellers.service")
wishlist_list = importlib.import_module("aos.api.wishlist.list")


class TestDynamicSqlSafety(FrappeTestCase):
    """Focused checks for SQL fragments that cannot be parameter-bound."""

    def setUp(self):
        frappe.local.response = {}

    def test_sql_identifier_guards_reject_malicious_fields(self):
        cursor = build_time_id_cursor(created_on="2026-01-01 00:00:00", name="ROW-001")

        with self.assertRaises(ValueError):
            build_cursor_where_clause(
                "s.creation; DROP TABLE `tabUser`; --",
                "s.name",
                cursor,
            )

        with self.assertRaises(ValueError):
            build_ranked_cursor_where_clause(
                "s.ranking_score",
                "s.creation",
                "s.name DESC; DROP TABLE `tabUser`; --",
                cursor,
            )

        with self.assertRaises(ValueError):
            require_dotted_sql_identifier("u.name OR 1=1")

    def test_cursor_values_are_bound_as_params_not_inlined(self):
        malicious_name = "SHORT-001'); DROP TABLE `tabUser`; --"
        cursor = encode_cursor(
            {
                "ranking_score": 42,
                "created_on": "2026-01-01 00:00:00",
                "name": malicious_name,
            }
        )

        clause, params = build_ranked_cursor_where_clause(
            score_field="s.ranking_score",
            created_field="s.creation",
            name_field="s.name",
            cursor=cursor,
        )

        self.assertNotIn("DROP TABLE", clause)
        self.assertNotIn(malicious_name, clause)
        self.assertIn(malicious_name, params)

    def test_candidate_id_fragments_drop_unsafe_external_ids(self):
        unsafe = "SHORT-001'); DROP TABLE `tabUser`; --"

        self.assertEqual(
            clean_safe_docnames(["SHORT-2026-00001", unsafe, "SHORT-2026-00001"]),
            ["SHORT-2026-00001"],
        )

        candidate_clause, order_clause = shorts_feed._short_candidate_sql(
            ["SHORT-2026-00001", unsafe]
        )
        ad_order_clause = ads_list._search_order_sql(["AD-2026-00001", unsafe])

        self.assertIn("SHORT-2026-00001", candidate_clause)
        self.assertIn("SHORT-2026-00001", order_clause)
        self.assertIn("AD-2026-00001", ad_order_clause)
        self.assertNotIn("DROP TABLE", candidate_clause)
        self.assertNotIn("DROP TABLE", order_clause)
        self.assertNotIn("DROP TABLE", ad_order_clause)

    def test_dynamic_alias_and_target_expression_are_allowlisted(self):
        with self.assertRaises(ValueError):
            shorts_library._select_short_rows_with_action_sql("sv; DROP TABLE `tabUser`; --")

        self.assertIn("sv.creation AS action_creation", shorts_library._select_short_rows_with_action_sql("sv"))

    def test_like_helpers_escape_wildcards(self):
        self.assertEqual(safe_like_contains("50%_off\\sale"), "%50\\%\\_off\\\\sale%")
        self.assertEqual(safe_like_prefix("50%_off\\sale"), "50\\%\\_off\\\\sale%")

    def test_ads_invalid_sort_is_rejected_before_sql(self):
        with (
            patch.object(ads_list, "rate_limit", return_value=None),
            patch.object(ads_list, "request_ip", return_value="127.0.0.1"),
            patch.object(ads_list, "resolve_market_context", return_value=("Kenya", "KES", None)),
            patch.object(ads_list, "current_user", return_value="Guest"),
            patch.object(ads_list.frappe.db, "sql", side_effect=AssertionError("SQL should not run")),
        ):
            response = ads_list.list_ads_impl(sort="recent; DROP TABLE `tabAOS Ad`; --")

        self.assertFalse(response.get("ok"))
        self.assertEqual(response.get("error"), "VALIDATION_ERROR")
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)

    def test_ads_invalid_cursor_is_rejected_before_sql(self):
        with (
            patch.object(ads_list, "rate_limit", return_value=None),
            patch.object(ads_list, "request_ip", return_value="127.0.0.1"),
            patch.object(ads_list, "resolve_market_context", return_value=("Kenya", "KES", None)),
            patch.object(ads_list, "current_user", return_value="Guest"),
            patch.object(ads_list.frappe.db, "sql", side_effect=AssertionError("SQL should not run")),
        ):
            response = ads_list.list_ads_impl(cursor="not-a-valid-cursor")

        self.assertFalse(response.get("ok"))
        self.assertEqual(response.get("error"), "INVALID_AD_CURSOR")
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)

    def test_wishlist_legacy_sort_parameter_is_rejected_before_sql(self):
        with (
            patch.object(wishlist_list, "rate_limit", return_value=None),
            patch.object(wishlist_list, "request_ip", return_value="127.0.0.1"),
            patch.object(wishlist_list, "require_login", return_value=("sql-test@example.com", None)),
            patch.object(wishlist_list, "resolve_market_context", return_value=("Kenya", "KES", None)),
            patch.object(wishlist_list.frappe.db, "sql", side_effect=AssertionError("SQL should not run")),
        ):
            response = wishlist_list.list_wishlist_impl(sort="recent; DROP TABLE `tabAOS Wishlist`; --")

        self.assertFalse(response.get("ok"))
        self.assertEqual(response.get("error"), "INVALID_WISHLIST_REQUEST")
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)

    def test_wishlist_invalid_cursor_is_rejected_before_listing_sql(self):
        with (
            patch.object(wishlist_list, "rate_limit", return_value=None),
            patch.object(wishlist_list, "request_ip", return_value="127.0.0.1"),
            patch.object(wishlist_list, "require_login", return_value=("sql-test@example.com", None)),
            patch.object(wishlist_list, "resolve_market_context", return_value=("Kenya", "KES", None)),
            patch.object(wishlist_list.frappe.db, "sql", side_effect=AssertionError("SQL should not run")),
        ):
            response = wishlist_list.list_wishlist_impl(cursor="not-a-valid-cursor")

        self.assertFalse(response.get("ok"))
        self.assertEqual(response.get("error"), "INVALID_WISHLIST_CURSOR")
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)

    def test_seller_search_escapes_like_wildcards(self):
        captured = {}

        def fake_sql(query, params=None, **kwargs):
            captured["query"] = query
            captured["params"] = tuple(params or ())
            return []

        with (
            patch.object(list_sellers, "rate_limit", return_value=None),
            patch.object(list_sellers, "request_ip", return_value="127.0.0.1"),
            patch.object(list_sellers, "current_user", return_value="Guest"),
            patch.object(seller_service.frappe.db, "sql", side_effect=fake_sql),
        ):
            response = list_sellers.list_sellers_impl(search="50%_off")

        self.assertTrue(response.get("ok"), response)
        self.assertIn("ESCAPE '\\\\'", captured["query"])
        self.assertNotIn("ESCAPE '\\'", captured["query"])
        self.assertIn("%50\\%\\_off%", captured["params"])

    def test_seller_invalid_sort_is_rejected_before_sql(self):
        with (
            patch.object(list_sellers, "rate_limit", return_value=None),
            patch.object(list_sellers, "request_ip", return_value="127.0.0.1"),
            patch.object(list_sellers, "current_user", return_value="Guest"),
            patch.object(seller_service.frappe.db, "sql", side_effect=AssertionError("SQL should not run")),
        ):
            response = list_sellers.list_sellers_impl(sort="rating; DROP TABLE `tabAOS Seller`; --")

        self.assertFalse(response.get("ok"))
        self.assertEqual(response.get("error"), "INVALID_SELLER_SORT")
        self.assertEqual(frappe.local.response.get("http_status_code"), 422)

    def test_sound_search_escapes_like_wildcards(self):
        captured = {}

        def fake_sql(query, params=None, **kwargs):
            captured["query"] = query
            captured["params"] = tuple(params or ())
            return []

        with (
            patch.object(shorts_sounds, "rate_limit", return_value=None),
            patch.object(shorts_sounds, "request_ip", return_value="127.0.0.1"),
            patch.object(shorts_sounds, "_get_optional_viewer", return_value=None),
            patch.object(shorts_sounds.frappe.db, "sql", side_effect=fake_sql),
        ):
            response = shorts_sounds.search_sounds_impl(q="50%_off")

        self.assertTrue(response.get("ok"), response)
        self.assertIn("ESCAPE", captured["query"])
        self.assertIn("%50\\%\\_off%", captured["params"])
