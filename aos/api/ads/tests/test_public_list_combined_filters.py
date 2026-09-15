from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from aos.api.ads.list_ads import _primary_order, list_ads_impl


class TestPublicListCombinedFilters(FrappeTestCase):
    def test_explicit_price_sort_controls_primary_order(self):
        primary = _primary_order(
            candidate_order="",
            sort="price_low",
            promotion_type="deal",
            conversion_available="conversion_available",
            price="current_price",
            verified="verified_rank",
        )

        self.assertTrue(primary.startswith("conversion_available DESC, current_price ASC"))
        self.assertLess(primary.index("current_price ASC"), primary.index("verified_rank"))
        self.assertNotIn("offer_percent", primary)

    def test_explicit_recent_sort_ignores_candidate_and_promotion_boosts(self):
        primary = _primary_order(
            candidate_order="FIELD(a.public_id, 'AD-1')",
            sort="recent",
            promotion_type="deal",
            conversion_available="conversion_available",
            price="current_price",
            verified="verified_rank",
        )

        self.assertEqual(primary, "a.creation DESC, a.public_id DESC")
        self.assertNotIn("FIELD(a.public_id", primary)
        self.assertNotIn("offer_percent", primary)
        self.assertNotIn("verified_rank", primary)

    def test_category_and_other_filters_share_one_query_with_final_geographic_rerank(self):
        settings = SimpleNamespace(base_currency="KES", flash_sale_window_days=7, fx_max_stale_hours=24)
        captured: dict[str, object] = {}

        def fake_sql(query, values, as_dict=False):
            captured["query"] = query
            captured["values"] = values
            captured["as_dict"] = as_dict
            return []

        def fake_conversion(*, amount_sql, fresh_after_param):
            del fresh_after_param
            amount = "current_price" if "CASE WHEN" in amount_sql else "original_price_converted"
            return {
                "currency": "%(display_currency)s",
                "available": "conversion_available",
                "rate": "1",
                "amount": amount,
            }

        with (
            patch("aos.api.ads.list_ads.rate_limit", return_value=None),
            patch("aos.api.ads.list_ads.resolve_market_context", return_value=("Kenya", "KES", None)),
            patch("aos.api.ads.list_ads.current_user", return_value="Guest"),
            patch("aos.api.ads.list_ads.get_aos_settings_snapshot", return_value=settings),
            patch("aos.api.ads.list_ads.resolve_category_filter_values", return_value=["Cars"]),
            patch("aos.api.ads.list_ads.sql_conversion_expressions", side_effect=fake_conversion),
            patch("aos.api.ads.list_ads.frappe.db.sql", side_effect=fake_sql),
        ):
            response = list_ads_impl(
                category="Cars",
                country="Kenya",
                location="Mombasa",
                sort="price_low",
                price_min="100",
                price_max="1000",
                rating_min="3",
                verified_seller=1,
                limit=20,
            )

        self.assertTrue(response["ok"])
        query = str(captured["query"])
        values = captured["values"]
        self.assertIn("a.category IN %(categories)s", query)
        self.assertIn("current_price >= %(price_min)s", query)
        self.assertIn("current_price <= %(price_max)s", query)
        self.assertIn("a.average_rating >= %(rating_min)s", query)
        self.assertIn("COALESCE(p.is_verified, 0) = 1", query)

        order_by = query.split("ORDER BY", 1)[1].split("LIMIT", 1)[0].strip()
        self.assertTrue(order_by.startswith("CASE WHEN %(country)s <> '' AND %(location)s <> ''"))
        self.assertLess(order_by.index("END ASC"), order_by.index("current_price ASC"))

        self.assertEqual(values["country"], "Kenya")
        self.assertEqual(values["location"], "Mombasa")
        self.assertEqual(values["categories"], ("Cars",))
        self.assertEqual(values["price_min"], "100.000000")
        self.assertEqual(values["price_max"], "1000.000000")
        self.assertEqual(values["rating_min"], "3.000000")
