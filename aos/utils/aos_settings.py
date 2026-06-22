"""AOS Settings helpers.

Centralizes reading the AOS Settings singleton to avoid scattered direct access.
Includes light caching because these values change rarely.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe


@dataclass(frozen=True)
class AOSSettingsSnapshot:
	# Localization
	default_currency: str | None
	default_language: str | None
	default_country: str | None

	# Foreign Exchange
	base_currency: str | None
	refresh_hours: int

	# Ads
	ad_expiry_days: int
	flash_sale_window_days: int

	# Image Search Service
	image_search_service_url: str
	image_search_service_timeout_seconds: int
	image_search_default_limit: int
	image_search_max_limit: int

	# Background Removal Service
	background_removal_service_url: str
	background_removal_service_timeout_seconds: int
	background_removal_max_image_bytes: int

	# Storage (MinIO)
	minio_endpoint: str | None
	minio_access_key: str | None
	minio_bucket: str
	minio_public_base_url: str
	minio_secure: int
	minio_upload_expiry_minutes: int
	minio_base_path: str

	# Connect (LiveKit)
	livekit_endpoint: str | None
	livekit_token_ttl_minutes: int

	# Translation
	translation_service_url: str
	translation_max_characters: int
	translation_service_timeout_seconds: int


def _clamp_int(val: object, default: int, min_value: int, max_value: int) -> int:
	"""Best-effort int parsing with bounds safety."""
	try:
		n = int(val)  # type: ignore[arg-type]
	except Exception:
		n = int(default)

	if n < min_value:
		return int(min_value)
	if n > max_value:
		return int(max_value)
	return int(n)


def _clean_url(value: object, default: str) -> str:
	"""Normalize a URL-like setting."""
	url = str(value or "").strip()

	if not url:
		return default

	return url.rstrip("/")


def _bounded_pair(
	*,
	default_limit: int,
	max_limit: int,
	absolute_min: int,
	absolute_max: int,
) -> tuple[int, int]:
	"""Ensure default limit never exceeds max limit."""
	clean_max = _clamp_int(
		max_limit,
		default=absolute_max,
		min_value=absolute_min,
		max_value=absolute_max,
	)
	clean_default = _clamp_int(
		default_limit,
		default=min(20, clean_max),
		min_value=absolute_min,
		max_value=clean_max,
	)

	return clean_default, clean_max


def get_aos_settings_snapshot(use_cache: bool = True) -> AOSSettingsSnapshot:
	cache = frappe.cache()
	key = "aos:settings:snapshot:v4"

	if use_cache:
		cached = cache.get_value(key)
		if (
			isinstance(cached, dict)
			and "image_search_service_url" in cached
			and "background_removal_service_url" in cached
			and "translation_service_url" in cached
		):
			return AOSSettingsSnapshot(**cached)

	s = frappe.get_single("AOS Settings")

	image_search_default_limit, image_search_max_limit = _bounded_pair(
		default_limit=_clamp_int(
			getattr(s, "image_search_default_limit", 20),
			default=20,
			min_value=1,
			max_value=100,
		),
		max_limit=_clamp_int(
			getattr(s, "image_search_max_limit", 100),
			default=100,
			min_value=1,
			max_value=100,
		),
		absolute_min=1,
		absolute_max=100,
	)

	snap = AOSSettingsSnapshot(
		# Localization
		default_currency=(s.default_currency or None),
		default_language=(s.default_language or None),
		default_country=(s.default_country or None),

		# Foreign Exchange
		base_currency=(s.base_currency or None),
		refresh_hours=_clamp_int(
			getattr(s, "refresh_hours", 12),
			default=12,
			min_value=1,
			max_value=24 * 7,
		),

		# Ads
		ad_expiry_days=_clamp_int(
			getattr(s, "ad_expiry_days", 30),
			default=30,
			min_value=1,
			max_value=365,
		),

		flash_sale_window_days=_clamp_int(
			getattr(s, "flash_sale_window_days", 7),
			default=7,
			min_value=1,
			max_value=60,
		),

		# Image Search Service
		image_search_service_url=_clean_url(
			getattr(s, "image_search_service_url", None),
			default="http://127.0.0.1:8110",
		),

		image_search_service_timeout_seconds=_clamp_int(
			getattr(s, "image_search_service_timeout_seconds", 20),
			default=20,
			min_value=1,
			max_value=120,
		),

		image_search_default_limit=image_search_default_limit,
		image_search_max_limit=image_search_max_limit,

		# Background Removal Service
		background_removal_service_url=_clean_url(
			getattr(s, "background_removal_service_url", None),
			default="http://127.0.0.1:8120",
		),

		background_removal_service_timeout_seconds=_clamp_int(
			getattr(s, "background_removal_service_timeout_seconds", 30),
			default=30,
			min_value=1,
			max_value=180,
		),

		background_removal_max_image_bytes=_clamp_int(
			getattr(s, "background_removal_max_image_bytes", 10 * 1024 * 1024),
			default=10 * 1024 * 1024,
			min_value=1,
			max_value=50 * 1024 * 1024,
		),

		# Storage (MinIO)
		minio_endpoint=(getattr(s, "minio_endpoint", None) or "localhost:9100"),

		minio_access_key=(getattr(s, "minio_access_key", None) or "minio"),

		minio_bucket=(getattr(s, "minio_bucket", None) or "shorts"),

		minio_public_base_url=(
			getattr(s, "minio_public_base_url", None)
			or "http://localhost:9100/shorts"
		),

		minio_secure=_clamp_int(
			getattr(s, "minio_secure", 0),
			default=0,
			min_value=0,
			max_value=1,
		),

		minio_upload_expiry_minutes=_clamp_int(
			getattr(s, "minio_upload_expiry_minutes", 10),
			default=10,
			min_value=1,
			max_value=60,
		),

		minio_base_path=(getattr(s, "minio_base_path", None) or "shorts"),

		# Connect (LiveKit)
		livekit_endpoint=(getattr(s, "livekit_endpoint", None) or None),

		livekit_token_ttl_minutes=_clamp_int(
			getattr(s, "livekit_token_ttl_minutes", 60),
			default=60,
			min_value=1,
			max_value=1440,
		),

		# Translation
		translation_service_url=_clean_url(
			getattr(s, "translation_service_url", None),
			default="http://127.0.0.1:8100",
		),

		translation_max_characters=_clamp_int(
			getattr(s, "translation_max_characters", 1000),
			default=1000,
			min_value=1,
			max_value=5000,
		),

		translation_service_timeout_seconds=_clamp_int(
			getattr(s, "translation_service_timeout_seconds", 10),
			default=30,
			min_value=1,
			max_value=60,
		),
	)

	try:
		cache.set_value(key, snap.__dict__, expires_in_sec=60 * 5)
	except Exception:
		pass

	return snap
