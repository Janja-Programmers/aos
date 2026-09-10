from __future__ import annotations

import os
from functools import lru_cache
from typing import Any


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


class Settings:
	"""Runtime settings actually consumed by the background-removal service."""

	def __init__(self) -> None:
		self.model_name = _get_str("BACKGROUND_REMOVAL_MODEL_NAME", "u2net")
		self.service_secret = _get_optional_str("BACKGROUND_REMOVAL_SERVICE_SECRET")
		self.max_image_bytes = _get_int(
			"BACKGROUND_REMOVAL_MAX_IMAGE_BYTES",
			10 * 1024 * 1024,
			minimum=1,
		)
		self.max_image_pixels = min(
			_get_int("BACKGROUND_REMOVAL_MAX_IMAGE_PIXELS", 16_000_000, minimum=1),
			40_000_000,
		)

	def public_dict(self) -> dict[str, Any]:
		"""Safe effective settings for health/readiness responses."""
		return {
			"model_name": self.model_name,
			"service_auth_configured": bool(self.service_secret),
			"max_image_bytes": self.max_image_bytes,
			"max_image_pixels": self.max_image_pixels,
		}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
	return Settings()
