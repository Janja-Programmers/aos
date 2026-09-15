from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from aos.api.ads.update import _existing_values
from aos.services.ads.errors import AdsValidationError
from aos.services.ads.validation import normalize_pricing, persisted_offer_value


class TestAdsOfferPersistence(TestCase):
    def test_currency_zero_sentinel_is_absent_during_document_revalidation(self):
        for value in (None, "", 0, 0.0, "0", "0.000000", Decimal("0.000000")):
            with self.subTest(value=value):
                self.assertIsNone(persisted_offer_value("Fixed", value))

        self.assertEqual(persisted_offer_value("Fixed", "12.500000"), "12.500000")
        self.assertIsNone(persisted_offer_value("Negotiable", "12.500000"))
        self.assertIsNone(persisted_offer_value("Contact for price", "12.500000"))

    def test_active_update_existing_values_drop_persisted_zero_offer_metadata(self):
        values = _existing_values(
            SimpleNamespace(
                category="CAT-1",
                price_type="Negotiable",
                price="100.000000",
                price_unit="",
                offer_price=Decimal("0.000000"),
                offer_start_date="2026-08-01",
                offer_end_date="2026-08-31",
            )
        )

        self.assertIsNone(values["offer_price"])
        self.assertIsNone(values["offer_start_date"])
        self.assertIsNone(values["offer_end_date"])

    def test_active_pricing_update_accepts_persisted_zero_but_public_zero_stays_invalid(self):
        pricing = {
            "pricing_requirement": "Required",
            "allowed_price_types": ["Fixed", "Negotiable"],
            "allowed_price_units": [],
        }
        existing = _existing_values(
            SimpleNamespace(
                category="CAT-1",
                price_type="Negotiable",
                price="100.000000",
                price_unit="",
                offer_price=0,
                offer_start_date=None,
                offer_end_date=None,
            )
        )
        with patch(
            "aos.services.ads.validation._catalog_schema",
            return_value=("CAT-1", [], pricing, False),
        ):
            normalized = normalize_pricing(
                {**existing, "price_type": "Negotiable", "price": "125"},
                category="CAT-1",
            )
            self.assertIsNone(normalized["offer_price"])

            with self.assertRaises(AdsValidationError):
                normalize_pricing(
                    {"price_type": "Negotiable", "price": "125", "offer_price": "0"},
                    category="CAT-1",
                )
