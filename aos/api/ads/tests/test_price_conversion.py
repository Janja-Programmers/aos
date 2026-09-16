from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

from aos.api.ads.serializers import serialize_ad_detail, serialize_ad_list_item


def _ad(**overrides):
    values = {
        "name": "AD-CONVERSION-TEST",
        "title": "Conversion test",
        "status": "Active",
        "country": "Kenya",
        "location": "LOC-TEST",
        "category": "Test",
        "seller": "SELLER-TEST",
        "currency": "KES",
        "display_currency": "KES",
        "requested_display_currency": "USD",
        "conversion_available": 0,
        "conversion_rate": None,
        "price": 1000,
        "original_price_converted": 1000,
        "current_price": 1000,
        "price_type": "Fixed",
        "is_offer_active": 0,
        "total_reviews": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class TestPriceConversionSerialization(TestCase):
    def test_list_and_wishlist_serializer_metadata_matches_fallback_number(self):
        payload = serialize_ad_list_item(_ad())
        self.assertEqual(payload["display_price"], 1000)
        self.assertEqual(payload["currency"], "KES")
        self.assertEqual(payload["display_currency"], "KES")
        self.assertNotIn("displayed_price_value", payload)
        self.assertNotIn("displayed_currency", payload)
        self.assertFalse(payload["price_conversion"]["available"])
        self.assertEqual(payload["price_conversion"]["reason"], "MISSING_EXCHANGE_RATE")

    def test_detail_serializer_metadata_matches_converted_number(self):
        payload = serialize_ad_detail(_ad(display_currency="USD", conversion_available=1, conversion_rate=0.00776, original_price_converted=7.76, current_price=7.76))
        self.assertEqual(payload["display_price"], 7.76)
        self.assertEqual(payload["currency"], "KES")
        self.assertEqual(payload["display_currency"], "USD")
        self.assertTrue(payload["price_conversion"]["available"])
        self.assertTrue(payload["price_conversion"]["converted"])
        self.assertAlmostEqual(payload["price_conversion"]["rate"], 0.00776)
