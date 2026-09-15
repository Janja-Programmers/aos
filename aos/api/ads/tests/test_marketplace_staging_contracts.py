from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aos.api.ads.serializers import serialize_ad_list_item
from aos.services.ads.constants import ALLOWED_PRICE_TYPES as AD_PRICE_TYPES
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import normalize_pricing
from aos.services.catalog.constants import ALLOWED_PRICE_TYPES as CATALOG_PRICE_TYPES


class TestMarketplaceStagingContracts(unittest.TestCase):
    def test_free_is_not_a_supported_price_type(self):
        expected = {"Fixed", "Negotiable", "Contact for price"}
        self.assertEqual(set(AD_PRICE_TYPES), expected)
        self.assertEqual(set(CATALOG_PRICE_TYPES), expected)
        self.assertNotIn("Free", AD_PRICE_TYPES)
        self.assertNotIn("Free", CATALOG_PRICE_TYPES)


    def test_contact_for_price_requires_explicit_service_catalog_configuration(self):
        with patch(
            "aos.services.ads.validation._catalog_schema",
            return_value=("Goods", [], {"pricing_requirement": "Optional", "allowed_price_types": []}, False),
        ):
            with self.assertRaises(AdsValidationError):
                normalize_pricing({"price_type": "Contact for price"}, category="Goods")

        with patch(
            "aos.services.ads.validation._catalog_schema",
            return_value=(
                "Services",
                [],
                {"pricing_requirement": "Optional", "allowed_price_types": ["Contact for price"]},
                True,
            ),
        ):
            pricing = normalize_pricing({"price_type": "Contact for price"}, category="Services")
        self.assertEqual(pricing["price_type"], "Contact for price")
        self.assertIsNone(pricing["price"])

    def test_public_ad_projection_keeps_location_id_and_adds_human_label(self):
        ad = SimpleNamespace(
            public_id="ad_example", title="Example", status="Active", country="Kenya",
            location="pi85c3avi4", location_name="Mombasa", category="Cars",
            currency="KES", display_currency="KES", requested_display_currency="KES",
            conversion_available=1, conversion_rate=1, price_type="Negotiable", price=100,
            original_price_converted=100, current_price=100, offer_price=None,
            offer_start_date=None, offer_end_date=None, offer_percent=0, price_unit="",
            average_rating=0, total_reviews=0, creation="2026-09-15 00:00:00", images=[],
            is_offer_active=False,
        )
        item = serialize_ad_list_item(ad)
        self.assertEqual(item["location"], "pi85c3avi4")
        self.assertEqual(item["location_name"], "Mombasa")


if __name__ == "__main__":
    unittest.main()
