from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}


def _get_str(name: str, default: str) -> str:
	value = os.getenv(name)
	if value is None:
		return default
	value = value.strip()
	return value or default


def _get_optional_str(name: str) -> str | None:
	value = os.getenv(name)
	if value is None:
		return None
	value = value.strip()
	return value or None


def _get_int(name: str, default: int, *, minimum: int | None = None) -> int:
	raw = _get_str(name, str(default))
	try:
		value = int(raw)
	except (TypeError, ValueError):
		value = default

	if minimum is not None:
		value = max(value, minimum)

	return value


def _get_float(name: str, default: float, *, minimum: float | None = None) -> float:
	raw = _get_str(name, str(default))
	try:
		value = float(raw)
	except (TypeError, ValueError):
		value = default

	if minimum is not None:
		value = max(value, minimum)

	return value


def _get_bool(name: str, default: bool) -> bool:
	raw = os.getenv(name)
	if raw is None:
		return default
	return raw.strip().lower() in _TRUE_VALUES


class Settings:
	"""Runtime configuration for the image-search service.

	Model/vector-store settings live here, outside the Frappe business backend.
	"""

	def __init__(self) -> None:
		self.service_name = _get_str("IMAGE_SEARCH_SERVICE_NAME", "aos-image-search")
		self.environment = _get_str("IMAGE_SEARCH_ENVIRONMENT", "development")
		self.debug = _get_bool("IMAGE_SEARCH_DEBUG", False)

		self.model_name = _get_str("IMAGE_SEARCH_MODEL_NAME", "ViT-B-32")
		self.pretrained = _get_str("IMAGE_SEARCH_PRETRAINED", "laion2b_s34b_b79k")
		self.device = _get_str("IMAGE_SEARCH_DEVICE", "cpu")
		self.vector_size = _get_int("IMAGE_SEARCH_VECTOR_SIZE", 512, minimum=1)

		self.qdrant_url = _get_str("IMAGE_SEARCH_QDRANT_URL", "http://qdrant:6333")
		self.qdrant_api_key = _get_optional_str("IMAGE_SEARCH_QDRANT_API_KEY")
		self.collection = _get_str("IMAGE_SEARCH_COLLECTION", "ads")

		self.default_limit = _get_int("IMAGE_SEARCH_LIMIT", 50, minimum=1)
		self.max_limit = _get_int("IMAGE_SEARCH_MAX_LIMIT", 100, minimum=1)
		self.score_threshold = _get_float(
			"IMAGE_SEARCH_SCORE_THRESHOLD",
			0.75,
			minimum=0.0,
		)
		self.primary_boost = _get_float(
			"IMAGE_SEARCH_PRIMARY_BOOST",
			0.02,
			minimum=0.0,
		)

		self.max_image_bytes = _get_int(
			"IMAGE_SEARCH_MAX_IMAGE_BYTES",
			10 * 1024 * 1024,
			minimum=1,
		)
		self.request_timeout_seconds = _get_float(
			"IMAGE_SEARCH_REQUEST_TIMEOUT_SECONDS",
			15.0,
			minimum=1.0,
		)

		self.internal_secret = _get_optional_str("IMAGE_SEARCH_INTERNAL_SECRET")
		self.short_classification_max_frames = min(
			_get_int("IMAGE_SEARCH_SHORT_CLASSIFICATION_MAX_FRAMES", 6, minimum=1),
			8,
		)
		self.short_classification_max_frame_bytes = _get_int(
			"IMAGE_SEARCH_SHORT_CLASSIFICATION_MAX_FRAME_BYTES", 1048576, minimum=65536
		)
		self.short_classification_max_total_bytes = _get_int(
			"IMAGE_SEARCH_SHORT_CLASSIFICATION_MAX_TOTAL_BYTES", 6291456, minimum=65536
		)
		self.short_classification_temperature = _get_float(
			"IMAGE_SEARCH_SHORT_CLASSIFICATION_TEMPERATURE", 0.05, minimum=0.01
		)
		self.short_classification_model_version = _get_str(
			"IMAGE_SEARCH_SHORT_CLASSIFICATION_MODEL_VERSION", "openclip-v1"
		)

		# Optional. Prefer sending absolute URLs from AOS backend. This exists only
		# as a safe fallback for /files/... values during local development.
		self.file_base_url = _get_optional_str("IMAGE_SEARCH_FILE_BASE_URL")

	def clamp_limit(self, value: int | None) -> int:
		if value is None:
			return self.default_limit
		return max(1, min(int(value), self.max_limit))

	def public_dict(self) -> dict[str, Any]:
		"""Safe config details for health/readiness responses."""
		return {
			"service": self.service_name,
			"environment": self.environment,
			"model_name": self.model_name,
			"pretrained": self.pretrained,
			"device": self.device,
			"vector_size": self.vector_size,
			"collection": self.collection,
			"default_limit": self.default_limit,
			"max_limit": self.max_limit,
			"score_threshold": self.score_threshold,
			"primary_boost": self.primary_boost,
			"max_image_bytes": self.max_image_bytes,
			"request_timeout_seconds": self.request_timeout_seconds,
			"short_classification_enabled": bool(self.internal_secret),
			"short_classification_max_frames": self.short_classification_max_frames,
			"short_classification_model_version": self.short_classification_model_version,
			"file_base_url_configured": bool(self.file_base_url),
		}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
	return Settings()
