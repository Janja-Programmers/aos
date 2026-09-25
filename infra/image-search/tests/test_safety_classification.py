from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from PIL import Image

from app.safety_classification import PROMPTS, SafetyImageClassifier


class _Runtime:
    def __init__(self, *, image_vector: np.ndarray, text_vectors: dict[str, np.ndarray]):
        self.image_vector = np.asarray(image_vector, dtype=np.float32)
        self.text_vectors = text_vectors

    def generate_text_embedding(self, prompt: str):
        for category, prompts in PROMPTS.items():
            if prompt in prompts:
                return self.text_vectors[category]
        raise AssertionError(f"unexpected prompt: {prompt}")

    def generate_image_embedding(self, _image):
        return self.image_vector


def _vectors(*, safe: tuple[float, float], weapons: tuple[float, float]):
    base = {
        category: np.asarray((0.0, 1.0), dtype=np.float32)
        for category in PROMPTS
    }
    base["safe"] = np.asarray(safe, dtype=np.float32)
    base["weapons"] = np.asarray(weapons, dtype=np.float32)
    return base


def _settings():
    return SimpleNamespace(model_name="ViT-B-32", short_classification_model_version="openclip-v1")


def test_classifier_reports_top_safe_even_when_global_safe_softmax_is_not_majority():
    runtime = _Runtime(
        image_vector=np.asarray((1.0, 0.0), dtype=np.float32),
        text_vectors=_vectors(safe=(1.0, 0.0), weapons=(0.8, 0.6)),
    )
    classifier = SafetyImageClassifier(settings=_settings(), runtime=runtime)
    result = classifier.classify([Image.new("RGB", (8, 8), "white")])

    assert result["top_category"] == "safe"
    assert result["top_confidence"] == result["safe_confidence"]
    assert result["margin"] >= 0.0


def test_classifier_reports_actual_unsafe_leader_without_fabricated_other_category():
    runtime = _Runtime(
        image_vector=np.asarray((1.0, 0.0), dtype=np.float32),
        text_vectors=_vectors(safe=(0.8, 0.6), weapons=(1.0, 0.0)),
    )
    classifier = SafetyImageClassifier(settings=_settings(), runtime=runtime)
    result = classifier.classify([Image.new("RGB", (8, 8), "white")])

    assert result["top_category"] == "weapons"
    assert result["top_confidence"] > result["safe_confidence"]
    assert all(signal["category"] != "other" for signal in result["signals"])
