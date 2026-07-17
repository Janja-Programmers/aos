from __future__ import annotations

from unittest import TestCase

from aos.services.currency_conversion import MISSING_EXCHANGE_RATE, convert_amount, sql_conversion_expressions


class TestCurrencyConversion(TestCase):
    def test_same_currency_never_converts(self):
        result = convert_amount(1000, "KES", "KES", None, None)
        self.assertEqual(result.amount, 1000)
        self.assertEqual(result.display_currency, "KES")
        self.assertTrue(result.available)
        self.assertFalse(result.converted)

    def test_cross_currency_requires_both_rates(self):
        result = convert_amount(1000, "KES", "USD", 129.0, 1.0)
        self.assertAlmostEqual(result.amount, 7.751938, places=6)
        self.assertEqual(result.display_currency, "USD")
        self.assertTrue(result.converted)
        self.assertIsNone(result.reason)

    def test_missing_source_rate_returns_original(self):
        result = convert_amount(1000, "KES", "USD", None, 1.0)
        self.assertEqual(result.amount, 1000)
        self.assertEqual(result.display_currency, "KES")
        self.assertFalse(result.available)
        self.assertEqual(result.reason, MISSING_EXCHANGE_RATE)

    def test_missing_target_rate_returns_original(self):
        result = convert_amount(1000, "KES", "USD", 129.0, None)
        self.assertEqual(result.amount, 1000)
        self.assertEqual(result.display_currency, "KES")
        self.assertFalse(result.available)

    def test_base_currency_source_is_implicitly_one(self):
        result = convert_amount(10, "USD", "KES", None, 129.0, base_currency="USD")
        self.assertEqual(result.amount, 1290)
        self.assertEqual(result.display_currency, "KES")
        self.assertTrue(result.available)

    def test_base_currency_target_is_implicitly_one(self):
        result = convert_amount(1290, "KES", "USD", 129.0, None, base_currency="USD")
        self.assertEqual(result.amount, 10)
        self.assertEqual(result.display_currency, "USD")
        self.assertTrue(result.available)

    def test_sql_contract_has_no_one_sided_ifnull_fallback(self):
        expressions = sql_conversion_expressions(amount_sql="a.price")
        combined = " ".join(expressions.values()).upper()
        self.assertNotIn("IFNULL", combined)
        self.assertIn("ELSE (A.PRICE)", combined)
        self.assertIn("BASE_CURRENCY", combined)
