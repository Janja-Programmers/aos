from __future__ import annotations

import unittest

from aos.services.sellers.errors import SellerValidationError
from aos.services.sellers.validation import (
    ensure_known_fields,
    normalize_about_business,
    normalize_business_category,
    normalize_expected_version,
    normalize_operating_hours,
    normalize_pagination,
    normalize_sort,
)


class TestSellerValidation(unittest.TestCase):
    def test_storefront_text_normalizes_unicode_and_whitespace(self):
        self.assertEqual(normalize_business_category("  Home   & Garden "), "Home & Garden")
        self.assertEqual(
            normalize_about_business("  Trusted   seller\r\n\r\n\r\nWorldwide "),
            "Trusted seller\n\nWorldwide",
        )

    def test_storefront_text_rejects_structured_null_control_html_and_script_inputs(self):
        for value in ({"x": 1}, ["x"], True, "bad\x00value", "bad\x01value", "hidden\u200btext", "<script>x</script>", "javascript:alert(1)"):
            with self.subTest(value=value), self.assertRaises(SellerValidationError):
                normalize_about_business(value)

    def test_storefront_text_is_bounded(self):
        with self.assertRaises(SellerValidationError):
            normalize_business_category("x" * 141)
        with self.assertRaises(SellerValidationError):
            normalize_about_business("x" * 2001)

    def test_operating_hours_are_unique_bounded_and_sorted(self):
        result = normalize_operating_hours(
            [
                {"day_of_week": "Friday", "is_open": True, "open_time": "09:00", "close_time": "17:00"},
                {"day_of_week": "Monday", "is_open": False},
            ]
        )
        self.assertEqual([row["day_of_week"] for row in result], ["Monday", "Friday"])
        self.assertEqual(result[1]["open_time"], "09:00:00")
        self.assertIsNone(result[0]["open_time"])

        invalid = (
            [{"day_of_week": "Monday", "is_open": True, "open_time": "17:00", "close_time": "09:00"}],
            [{"day_of_week": "Monday", "is_open": False}, {"day_of_week": "Monday", "is_open": False}],
            [{"day_of_week": "Funday", "is_open": False}],
            [{"day_of_week": "Monday", "is_open": True, "open_time": "x", "close_time": "17:00"}],
            [{"day_of_week": "Monday", "is_open": False, "attacker": "value"}],
        )
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(SellerValidationError):
                normalize_operating_hours(value)

    def test_pagination_and_version_are_strict(self):
        self.assertEqual(normalize_pagination({"limit": 20}), (20, ""))
        self.assertEqual(normalize_pagination({"limit": 20, "cursor": "abc"}), (20, "abc"))
        self.assertEqual(normalize_expected_version("0"), 0)
        for payload in ({"limit": 0}, {"limit": 51}, {"limit": 2.5}, {"limit": True}, {"cursor": True}, {"cursor": "x" * 2049}):
            with self.subTest(payload=payload), self.assertRaises(SellerValidationError):
                normalize_pagination(payload)
        for value in (-1, True, 1.5, "x"):
            with self.subTest(value=value), self.assertRaises(SellerValidationError):
                normalize_expected_version(value)

    def test_sort_and_unknown_fields_are_rejected(self):
        self.assertEqual(normalize_sort(None), "recommended")
        for value in ("distance", "rating desc", "rating; DROP TABLE x"):
            with self.subTest(value=value), self.assertRaises(SellerValidationError):
                normalize_sort(value)
        with self.assertRaises(SellerValidationError):
            ensure_known_fields({"business_category": "Cars", "status": "Active"}, {"business_category"})
