from __future__ import annotations

import math
import threading
from typing import Iterable

import numpy as np
from PIL import Image

from .config import Settings, get_settings
from .embedding import EmbeddingRuntime, get_embedding_runtime

MODES = ("shop", "geo", "vibes", "learn")
PROMPTS: dict[str, tuple[str, ...]] = {
	"shop": (
		"a product being demonstrated for sale",
		"shopping, retail merchandise, prices or an online store",
		"a product review, unboxing or buying recommendation",
		"clothing, electronics, beauty or household goods displayed for purchase",
	),
	"geo": (
		"a travel destination, landmark or geographic place",
		"a city street, road, route, map or local area guide",
		"tourism, outdoor scenery, a beach, mountain, park or building",
		"content focused on where a place is and what it looks like",
	),
	"vibes": (
		"music, dance, comedy, entertainment or a social trend",
		"a lifestyle vlog, celebration, fashion, beauty or fun performance",
		"people dancing, singing, joking, partying or doing a challenge",
		"general social entertainment content",
	),
	"learn": (
		"an educational lesson, tutorial or teacher explaining a topic",
		"mathematics, science, technology, engineering or a whiteboard lesson",
		"a how-to guide, demonstration of a skill or step by step instruction",
		"business, coding, finance, language or academic learning content",
	),
}


class ShortClassificationError(RuntimeError):
	pass


class ShortFrameClassifier:
	def __init__(
		self,
		settings: Settings | None = None,
		runtime: EmbeddingRuntime | None = None,
	) -> None:
		self.settings = settings or get_settings()
		self.runtime = runtime or get_embedding_runtime()
		self._prompt_vectors: dict[str, np.ndarray] | None = None
		self._lock = threading.Lock()

	def _prompts(self) -> dict[str, np.ndarray]:
		if self._prompt_vectors is not None:
			return self._prompt_vectors
		with self._lock:
			if self._prompt_vectors is not None:
				return self._prompt_vectors
			vectors: dict[str, np.ndarray] = {}
			for mode in MODES:
				rows = [np.asarray(self.runtime.generate_text_embedding(prompt), dtype=np.float32) for prompt in PROMPTS[mode]]
				mean = np.mean(np.stack(rows, axis=0), axis=0)
				norm = float(np.linalg.norm(mean))
				if norm <= 0 or not math.isfinite(norm):
					raise ShortClassificationError("Invalid text embedding")
				vectors[mode] = mean / norm
			self._prompt_vectors = vectors
			return vectors

	def classify(self, images: Iterable[Image.Image]) -> dict:
		frames = list(images)
		if not frames:
			raise ShortClassificationError("At least one frame is required")
		prompt_vectors = self._prompts()
		per_frame: list[np.ndarray] = []
		for image in frames:
			image_vector = np.asarray(self.runtime.generate_image_embedding(image), dtype=np.float32)
			if image_vector.ndim != 1 or image_vector.size == 0:
				raise ShortClassificationError("Invalid frame embedding")
			logits = np.asarray(
				[float(np.dot(image_vector, prompt_vectors[mode])) for mode in MODES],
				dtype=np.float64,
			)
			per_frame.append(logits)

		mean_logits = np.mean(np.stack(per_frame, axis=0), axis=0)
		temperature = max(float(self.settings.short_classification_temperature), 0.01)
		scaled = mean_logits / temperature
		scaled = scaled - np.max(scaled)
		exp = np.exp(scaled)
		probabilities = exp / np.sum(exp)
		scores = {mode: round(float(probabilities[index]), 6) for index, mode in enumerate(MODES)}
		mode = max(MODES, key=lambda item: scores[item])
		ordered = sorted(scores.values(), reverse=True)
		margin = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
		return {
			"status": "ready",
			"mode": mode,
			"confidence": scores[mode],
			"margin": round(float(margin), 6),
			"scores": scores,
			"model": self.settings.model_name,
			"model_version": self.settings.short_classification_model_version,
			"frame_count": len(frames),
		}


_classifier: ShortFrameClassifier | None = None
_classifier_lock = threading.Lock()


def get_short_classifier() -> ShortFrameClassifier:
	global _classifier
	if _classifier is None:
		with _classifier_lock:
			if _classifier is None:
				_classifier = ShortFrameClassifier()
	return _classifier
