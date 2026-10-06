from __future__ import annotations

import unittest

from aos.services.reviews.errors import ReviewValidationError
from aos.services.reviews.pagination import decode_cursor, encode_cursor, query_fingerprint
from aos.services.reviews.validation import (
    ensure_known_fields,
    normalize_comment,
    normalize_flag,
    normalize_images,
    normalize_limit,
    normalize_rating,
    normalize_title,
    normalize_version,
)


class TestReviewValidation(unittest.TestCase):
    def test_rating_accepts_only_whole_stars_in_five_star_scale(self):
        self.assertEqual(normalize_rating("5"), 5)
        self.assertEqual(normalize_rating(1), 1)
        for value in (0, 6, -1, 2.5, "NaN", "Infinity", True, {}):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_rating(value)

    def test_review_text_is_normalized_bounded_and_safe(self):
        self.assertEqual(normalize_title("  Great   seller  "), "Great seller")
        self.assertEqual(normalize_comment("Fast   delivery\n\n\nThank you"), "Fast delivery\n\nThank you")
        for value in ("bad\x00text", "hidden\u200btext", "<script>x</script>", "javascript:alert(1)"):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_comment(value)
        with self.assertRaises(ReviewValidationError):
            normalize_title("x" * 121)
        with self.assertRaises(ReviewValidationError):
            normalize_comment("x" * 2001)

    def test_media_inputs_are_bounded_unique_canonical_ids(self):
        values = ["MEDIA-00000000000000000000000000000001", "MEDIA-00000000000000000000000000000002"]
        self.assertEqual(normalize_images(values), values)
        for value in ([{"id": values[0]}], ["https://example.com/x.jpg"], [values[0], values[0]], [f"MEDIA-{i + 1:032x}" for i in range(6)]):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_images(value)

    def test_limit_flags_and_versions_are_strict(self):
        self.assertEqual(normalize_limit("20"), 20)
        self.assertTrue(normalize_flag("true", field="with_media"))
        self.assertFalse(normalize_flag("0", field="with_media"))
        self.assertEqual(normalize_version("2026-09-18 12:34:56.123456"), "2026-09-18 12:34:56.123456")
        for value in (0, 51, True, 2.5, "x"):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                normalize_limit(value)
        with self.assertRaises(ReviewValidationError):
            normalize_flag("maybe", field="with_media")

    def test_query_bound_cursor_rejects_malformed_and_mismatched_queries(self):
        key = query_fingerprint({"ad_id": "ad_abc", "rating": 5})
        cursor = encode_cursor(scope="public", sort="newest", query_key=key, values=["2026-09-18 12:00:00", "review_abc"])
        self.assertEqual(
            decode_cursor(cursor, scope="public", sort="newest", query_key=key, expected_keys=2),
            ["2026-09-18 12:00:00", "review_abc"],
        )
        for value in ("not-base64", encode_cursor(scope="public", sort="newest", query_key="other", values=["2026-09-18 12:00:00", "review_abc"])):
            with self.subTest(value=value), self.assertRaises(ReviewValidationError):
                decode_cursor(value, scope="public", sort="newest", query_key=key, expected_keys=2)

    def test_unknown_fields_are_rejected(self):
        with self.assertRaises(ReviewValidationError):
            ensure_known_fields({"rating": 5, "reviewer": "attacker@example.com"}, {"rating"})

