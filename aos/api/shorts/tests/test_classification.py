from __future__ import annotations

from types import SimpleNamespace

from frappe.tests.utils import FrappeTestCase

from aos.services.shorts.classification import apply_visual_result, classify_for_publish, normalize_scores


class TestShortAutomaticClassification(FrappeTestCase):
    def test_math_video_is_classified_as_learn(self):
        result = classify_for_publish(
            visual_scores={"shop": 0.03, "geo": 0.04, "vibes": 0.08, "learn": 0.85},
            caption="Solving quadratic equations step by step",
            hashtags=["math", "stem"],
            has_shop_context=False,
            legacy_content_mode="vibes",
        )
        self.assertEqual(result.mode, "learn")
        self.assertIn(result.source, {"automatic_visual", "automatic_visual_text"})

    def test_legacy_creator_mode_is_not_authoritative(self):
        result = classify_for_publish(
            visual_scores={"shop": 0.02, "geo": 0.90, "vibes": 0.05, "learn": 0.03},
            caption="",
            hashtags=[],
            has_shop_context=False,
            legacy_content_mode="learn",
        )
        self.assertEqual(result.mode, "geo")

    def test_shop_requires_validated_commerce_context(self):
        without_ad = classify_for_publish(
            visual_scores={"shop": 1.0, "geo": 0.0, "vibes": 0.0, "learn": 0.0},
            caption="product for sale",
            hashtags=["shop"],
            has_shop_context=False,
            legacy_content_mode="shop",
        )
        self.assertNotEqual(without_ad.mode, "shop")

        with_ad = classify_for_publish(
            visual_scores={},
            caption="",
            hashtags=[],
            has_shop_context=True,
            legacy_content_mode=None,
        )
        self.assertEqual(with_ad.mode, "shop")
        self.assertEqual(with_ad.source, "commerce_context")
        self.assertEqual(with_ad.confidence, 1.0)

    def test_all_is_never_a_persisted_classification(self):
        scores = normalize_scores({"all": 1, "learn": 1})
        self.assertNotIn("all", scores)
        self.assertEqual(max(scores, key=scores.get), "learn")

    def test_reprocessing_does_not_overwrite_final_shop_classification(self):
        short = SimpleNamespace(
            content_mode="shop",
            classification_status="ready",
            classification_source="commerce_context",
            classification_confidence=1.0,
            classification_visual_scores=None,
            classification_scores='{"shop":1.0}',
            classification_model="aos-short-fusion",
            classification_model_version="1",
            classified_at="2026-08-01 00:00:00",
        )
        apply_visual_result(
            short,
            {
                "status": "ready",
                "scores": {"shop": 0.01, "geo": 0.01, "vibes": 0.03, "learn": 0.95},
                "model": "synthetic",
                "model_version": "test-v2",
            },
        )
        self.assertEqual(short.content_mode, "shop")
        self.assertEqual(short.classification_source, "commerce_context")
        self.assertIn('"learn":0.95', short.classification_visual_scores)
