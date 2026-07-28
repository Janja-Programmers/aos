from __future__ import annotations

import unittest

from aos.services.reviews.errors import ReviewValidationError
from aos.services.reviews.validation import (
    ensure_known_fields,
    normalize_comment,
    normalize_images,
    normalize_pagination,
    normalize_rating,
    normalize_report_reason,
    normalize_title,
)


class TestReviewValidation(unittest.TestCase):
    def test_rating_accepts_only_whole_stars_in_five_star_scale(self):
        self.assertEqual(normalize_rating("5"), 5)
        self.assertEqual(normalize_rating(1), 1)
        for value in (0, 6, -1, 2.5, "NaN", "Infinity", True, {}):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_rating(value)

    def test_review_text_normalizes_unicode_and_whitespace(self):
        self.assertEqual(normalize_title("  Great   seller  "), "Great seller")
        self.assertEqual(normalize_comment("Fast   delivery\n\n\nThank you"), "Fast delivery\n\nThank you")

    def test_review_text_rejects_null_control_html_and_script_schemes(self):
        for value in (
            "bad\x00text",
            "bad\x01text",
            "hidden\u200btext",
            "direction\u202eoverride",
            "<script>alert(1)</script>",
            "javascript:alert(1)",
        ):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_comment(value)

    def test_review_text_is_bounded(self):
        with self.assertRaises(ReviewValidationError):
            normalize_title("x" * 121)
        with self.assertRaises(ReviewValidationError):
            normalize_comment("x" * 2001)

    def test_review_text_rejects_spam_patterns_without_changing_normal_language(self):
        with self.assertRaises(ReviewValidationError):
            normalize_comment("a" * 40)
        with self.assertRaises(ReviewValidationError):
            normalize_comment(" ".join(["https://example.com"] * 4))
        self.assertEqual(normalize_comment("Très bon service — شكراً"), "Très bon service — شكراً")

    def test_media_inputs_are_bounded_unique_and_media_ids_only(self):
        self.assertEqual(normalize_images(["MEDIA-ABCDEF", {"media_id": "MEDIA-GHIJKL"}]), ["MEDIA-ABCDEF", "MEDIA-GHIJKL"])
        for value in (["https://example.com/x.jpg"], ["MEDIA-ABCDEF", "MEDIA-ABCDEF"], [f"MEDIA-ABC{i:03d}" for i in range(6)]):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_images(value)

    def test_pagination_is_bounded(self):
        self.assertEqual(normalize_pagination({"limit": 20, "offset": 0}), (20, 0))
        for payload in (
            {"limit": 0},
            {"limit": 51},
            {"offset": -1},
            {"offset": 10001},
            {"limit": "x"},
            {"limit": 2.5},
            {"limit": True},
            {"offset": 1.5},
        ):
            with self.subTest(payload=payload), self.assertRaises(ReviewValidationError):
                normalize_pagination(payload)

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ReviewValidationError):
            ensure_known_fields({"rating": 5, "reviewer": "attacker@example.com"}, {"rating"})

    def test_report_reason_is_normalized_for_central_active_reason_validation(self):
        self.assertEqual(normalize_report_reason("  Inappropriate Content  "), "Inappropriate Content")
        for value in ("", "bad\x00reason", "<script>bad</script>"):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_report_reason(value)
