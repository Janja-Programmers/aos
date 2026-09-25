from __future__ import annotations

import math
import threading
from typing import Iterable

import numpy as np
from PIL import Image

from .config import Settings, get_settings
from .embedding import EmbeddingRuntime, get_embedding_runtime

PROMPTS: dict[str, tuple[str, ...]] = {
    "safe": (
        "ordinary safe marketplace or social media content",
        "a normal product, person, landscape or everyday scene without prohibited content",
    ),
    "pornography": ("pornographic explicit sexual imagery", "explicit pornography"),
    "sexual_explicit": ("explicit sexual activity", "graphic sexual content"),
    "nudity": ("visible adult nudity with exposed intimate body parts", "prohibited nude imagery"),
    "weapons": ("a firearm, gun, combat weapon or weapon being prominently displayed", "a dangerous weapon"),
    "violence": ("physical assault or violent attack", "violent injury or fighting"),
    "graphic_violence": ("graphic gore, blood or severe visible injury", "graphic violent injury"),
    "hate": ("visual extremist hate propaganda or hateful symbols", "hate group propaganda"),
    "drugs": ("illegal hard drugs or narcotics prepared for sale", "cocaine heroin or methamphetamine"),
    "illegal_goods": ("counterfeit identity documents or illegal goods for sale", "illegal contraband"),
}
SEVERITY = {
    "pornography": "critical", "sexual_explicit": "high", "nudity": "high",
    "weapons": "high", "violence": "high", "graphic_violence": "critical",
    "hate": "critical", "drugs": "high", "illegal_goods": "high",
}


class SafetyClassificationError(RuntimeError):
    pass


class SafetyImageClassifier:
    def __init__(self, settings: Settings | None = None, runtime: EmbeddingRuntime | None = None) -> None:
        self.settings = settings or get_settings()
        self.runtime = runtime or get_embedding_runtime()
        self._vectors: dict[str, np.ndarray] | None = None
        self._lock = threading.Lock()

    def _prompts(self) -> dict[str, np.ndarray]:
        if self._vectors is not None:
            return self._vectors
        with self._lock:
            if self._vectors is not None:
                return self._vectors
            vectors: dict[str, np.ndarray] = {}
            for category, prompts in PROMPTS.items():
                rows = [np.asarray(self.runtime.generate_text_embedding(prompt), dtype=np.float32) for prompt in prompts]
                mean = np.mean(np.stack(rows, axis=0), axis=0)
                norm = float(np.linalg.norm(mean))
                if norm <= 0 or not math.isfinite(norm):
                    raise SafetyClassificationError("Invalid safety text embedding")
                vectors[category] = mean / norm
            self._vectors = vectors
            return vectors

    def classify(self, images: Iterable[Image.Image]) -> dict:
        frames = list(images)
        if not frames:
            raise SafetyClassificationError("At least one image is required")
        vectors = self._prompts()
        categories = tuple(PROMPTS)
        per_image: list[np.ndarray] = []
        for image in frames:
            image_vector = np.asarray(self.runtime.generate_image_embedding(image), dtype=np.float32)
            if image_vector.ndim != 1 or image_vector.size == 0:
                raise SafetyClassificationError("Invalid image embedding")
            per_image.append(np.asarray([float(np.dot(image_vector, vectors[c])) for c in categories], dtype=np.float64))

        # Use the maximum category probability across images so one severe frame
        # cannot be averaged away by many safe frames.
        per_image_probabilities: list[np.ndarray] = []
        for logits in per_image:
            scaled = logits / 0.07
            scaled -= np.max(scaled)
            exp = np.exp(scaled)
            per_image_probabilities.append(exp / np.sum(exp))
        matrix = np.stack(per_image_probabilities, axis=0)
        maxima = np.max(matrix, axis=0)
        scores = {category: float(maxima[index]) for index, category in enumerate(categories)}
        ranking = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        top_category, top_confidence = ranking[0]
        runner_up_confidence = ranking[1][1] if len(ranking) > 1 else 0.0
        margin = max(0.0, min(top_confidence - runner_up_confidence, 1.0))
        signals = [
            {"category": category, "confidence": round(scores[category], 6), "severity": SEVERITY[category]}
            for category in categories if category != "safe" and scores[category] >= 0.20
        ]
        return {
            "status": "ready",
            "signals": signals,
            "safe_confidence": round(scores["safe"], 6),
            "top_category": top_category,
            "top_confidence": round(top_confidence, 6),
            "margin": round(margin, 6),
            "model": self.settings.model_name,
            "model_version": getattr(self.settings, "short_classification_model_version", "openclip-v1"),
            "image_count": len(frames),
        }


_classifier: SafetyImageClassifier | None = None
_classifier_lock = threading.Lock()


def get_safety_classifier() -> SafetyImageClassifier:
    global _classifier
    if _classifier is None:
        with _classifier_lock:
            if _classifier is None:
                _classifier = SafetyImageClassifier()
    return _classifier
