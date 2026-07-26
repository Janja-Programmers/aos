from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from aos.api.ads.list_ads import _build_order_by, list_ads_impl


class TestPublicListCombinedFilters(FrappeTestCase):
    def test_explicit_price_sort_precedes_discovery_boosts(self):
        order_by = _build_order_by(
            candidate_order="",
            cursor="",
            sort="price_low",
            promotion_type="deal",
            geo_boost="geo_rank",
            verified_boost="verified_rank",
            conversion_available_sql="conversion_available",
            current_price_sql="current_price",
        )

        self.assertLess(order_by.index("current_price ASC"), order_by.index("geo_rank"))
        self.assertLess(order_by.index("current_price ASC"), order_by.index("verified_rank"))
        self.assertNotIn("offer_percent", order_by)

    def test_explicit_recent_sort_precedes_discovery_boosts(self):
        order_by = _build_order_by(
            candidate_order="FIELD(a.name, 'AD-1')",
            cursor="",
            sort="recent",
            promotion_type="deal",
            geo_boost="geo_rank",
            verified_boost="verified_rank",
            conversion_available_sql="conversion_available",
            current_price_sql="current_price",
        )

        self.assertTrue(order_by.startswith("a.creation DESC"))
        self.assertNotIn("FIELD(a.name", order_by)
        self.assertNotIn("offer_percent", order_by)

    def test_category_and_other_filters_are_forwarded_to_one_sql_query(self):
        settings = SimpleNamespace(base_currency="KES", flash_sale_window_days=7)
        captured: dict[str, object] = {}

        def fake_sql(query, values, as_dict=False):
            captured["query"] = query
            captured["values"] = values
            captured["as_dict"] = as_dict
            return []

        with (
            patch("aos.api.ads.list_ads.rate_limit", return_value=None),
            patch("aos.api.ads.list_ads.resolve_market_context", return_value=("Kenya", "KES", None)),
            patch("aos.api.ads.list_ads.current_user", return_value="Guest"),
            patch("aos.api.ads.list_ads.get_aos_settings_snapshot", return_value=settings),
            patch("aos.api.ads.list_ads.resolve_category_filter_values", return_value=["Cars"]),
            patch("aos.api.ads.list_ads.frappe.db.sql", side_effect=fake_sql),
        ):
            response = list_ads_impl(
                category="Cars",
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
        self.assertIn(">= %(price_min)s", query)
        self.assertIn("<= %(price_max)s", query)
        self.assertIn("a.average_rating >= %(rating_min)s", query)
        self.assertIn("COALESCE(p.is_verified, 0) = 1", query)
        self.assertIn("ASC", query.split("ORDER BY", 1)[1])
        self.assertEqual(values["categories"], ("Cars",))
        self.assertEqual(values["price_min"], "100.000000")
        self.assertEqual(values["price_max"], "1000.000000")
        self.assertEqual(values["rating_min"], "3.000000")
