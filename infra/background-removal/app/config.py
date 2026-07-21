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
	"""Runtime configuration for the background-removal service.

	Background-removal model/runtime settings live here, outside the Frappe
	business backend.
	"""

	def __init__(self) -> None:
		self.service_name = _get_str(
			"BACKGROUND_REMOVAL_SERVICE_NAME",
			"aos-background-removal",
		)
		self.environment = _get_str("BACKGROUND_REMOVAL_ENVIRONMENT", "development")
		self.debug = _get_bool("BACKGROUND_REMOVAL_DEBUG", False)

		self.model_name = _get_str("BACKGROUND_REMOVAL_MODEL_NAME", "u2net")
		self.device = _get_str("BACKGROUND_REMOVAL_DEVICE", "cpu")
		self.model_path = _get_optional_str("BACKGROUND_REMOVAL_MODEL_PATH")

		self.max_image_bytes = _get_int(
			"BACKGROUND_REMOVAL_MAX_IMAGE_BYTES",
			10 * 1024 * 1024,
			minimum=1,
		)
		self.request_timeout_seconds = _get_float(
			"BACKGROUND_REMOVAL_REQUEST_TIMEOUT_SECONDS",
			30.0,
			minimum=1.0,
		)

		self.output_format = _get_str("BACKGROUND_REMOVAL_OUTPUT_FORMAT", "png").lower()
		if self.output_format != "png":
			self.output_format = "png"

	def public_dict(self) -> dict[str, Any]:
		"""Safe config details for health/readiness responses."""
		return {
			"service": self.service_name,
			"environment": self.environment,
			"model_name": self.model_name,
			"device": self.device,
			"model_path_configured": bool(self.model_path),
			"max_image_bytes": self.max_image_bytes,
			"request_timeout_seconds": self.request_timeout_seconds,
			"output_format": self.output_format,
		}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
	return Settings()
