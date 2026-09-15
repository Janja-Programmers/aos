"""AOS Settings helpers.

Centralizes safe, admin-editable AOS Settings values.

Runtime infrastructure config, secrets, internal service URLs, buckets, and
external API keys belong in aos_config.py and are read from .env/environment
variables.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

AOS_SETTINGS_CACHE_KEY = "aos:settings:snapshot:v9"


@dataclass(frozen=True)
class AOSSettingsSnapshot:
	# Localization
	default_currency: str | None
	default_language: str | None
	default_country: str | None

	# Foreign Exchange business rules
	base_currency: str | None
	refresh_hours: int
	fx_max_stale_hours: int

	# Authentication public app identifiers
	google_oauth_client_ids: str | None
	apple_oauth_client_ids: str | None

	# Media
	media_presigned_upload_expiry_minutes: int
	background_removal_service_timeout_seconds: int
	background_removal_max_image_bytes: int

	# Ads
	ad_expiry_days: int
	flash_sale_window_days: int

	# Image Search behavior
	image_search_service_timeout_seconds: int
	image_search_default_limit: int
	image_search_max_limit: int

	# Connect / LiveKit / Translation
	livekit_token_ttl_minutes: int
	livekit_live_token_ttl_minutes: int
	translation_max_characters: int
	translation_service_timeout_seconds: int


def _clamp_int(val: object, default: int, min_value: int, max_value: int) -> int:
	"""Best-effort int parsing with bounds safety."""
	try:
		number = int(val)  # type: ignore[arg-type]
	except Exception:
		number = int(default)

	if number < min_value:
		return int(min_value)
	if number > max_value:
		return int(max_value)
	return int(number)


def _text_or_none(value: object) -> str | None:
	text = str(value or "").strip()
	return text or None


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


def _get_field(doc: object, fieldname: str, default: object = None) -> object:
	return getattr(doc, fieldname, default)


def get_aos_settings_snapshot(use_cache: bool = True) -> AOSSettingsSnapshot:
	cache = None
	try:
		cache = frappe.cache()
	except Exception:
		pass
	key = AOS_SETTINGS_CACHE_KEY

	if use_cache and cache is not None:
		try:
			cached = cache.get_value(key)
			if isinstance(cached, dict) and cached.get("_schema") == "v9":
				payload = dict(cached)
				payload.pop("_schema", None)
				return AOSSettingsSnapshot(**payload)
		except Exception:
			pass

	settings = frappe.get_single("AOS Settings")

	image_search_default_limit, image_search_max_limit = _bounded_pair(
		default_limit=_clamp_int(
			_get_field(settings, "image_search_default_limit", 20),
			default=20,
			min_value=1,
			max_value=100,
		),
		max_limit=_clamp_int(
			_get_field(settings, "image_search_max_limit", 100),
			default=100,
			min_value=1,
			max_value=100,
		),
		absolute_min=1,
		absolute_max=100,
	)

	snap = AOSSettingsSnapshot(
		# Localization
		default_currency=_text_or_none(_get_field(settings, "default_currency")),
		default_language=_text_or_none(_get_field(settings, "default_language")),
		default_country=_text_or_none(_get_field(settings, "default_country")),
		# Foreign Exchange business rules
		base_currency=_text_or_none(_get_field(settings, "base_currency")),
		refresh_hours=_clamp_int(
			_get_field(settings, "refresh_hours", 12),
			default=12,
			min_value=1,
			max_value=24 * 7,
		),
		fx_max_stale_hours=_clamp_int(
			_get_field(settings, "fx_max_stale_hours", 24),
			default=24,
			min_value=1,
			max_value=24 * 30,
		),
		# Authentication public app identifiers
		google_oauth_client_ids=_text_or_none(_get_field(settings, "google_oauth_client_ids")),
		apple_oauth_client_ids=_text_or_none(_get_field(settings, "apple_oauth_client_ids")),
		# Media
		media_presigned_upload_expiry_minutes=_clamp_int(
			_get_field(settings, "media_presigned_upload_expiry_minutes", 10),
			default=10,
			min_value=1,
			max_value=60,
		),
		background_removal_service_timeout_seconds=_clamp_int(
			_get_field(settings, "background_removal_service_timeout_seconds", 30),
			default=30,
			min_value=1,
			max_value=180,
		),
		background_removal_max_image_bytes=_clamp_int(
			_get_field(settings, "background_removal_max_image_bytes", 10 * 1024 * 1024),
			default=10 * 1024 * 1024,
			min_value=1,
			max_value=10 * 1024 * 1024,
		),
		# Ads
		ad_expiry_days=_clamp_int(
			_get_field(settings, "ad_expiry_days", 30),
			default=30,
			min_value=1,
			max_value=365,
		),
		flash_sale_window_days=_clamp_int(
			_get_field(settings, "flash_sale_window_days", 7),
			default=7,
			min_value=1,
			max_value=60,
		),
		# Image Search behavior
		image_search_service_timeout_seconds=_clamp_int(
			_get_field(settings, "image_search_service_timeout_seconds", 20),
			default=20,
			min_value=1,
			max_value=120,
		),
		image_search_default_limit=image_search_default_limit,
		image_search_max_limit=image_search_max_limit,
		# Connect / LiveKit / Translation
		livekit_token_ttl_minutes=_clamp_int(
			_get_field(settings, "livekit_token_ttl_minutes", 60),
			default=60,
			min_value=1,
			max_value=1440,
		),
		livekit_live_token_ttl_minutes=_clamp_int(
			_get_field(settings, "livekit_live_token_ttl_minutes", 15),
			default=15,
			min_value=1,
			max_value=30,
		),
		translation_max_characters=_clamp_int(
			_get_field(settings, "translation_max_characters", 1000),
			default=1000,
			min_value=1,
			max_value=5000,
		),
		translation_service_timeout_seconds=_clamp_int(
			_get_field(settings, "translation_service_timeout_seconds", 30),
			default=30,
			min_value=1,
			max_value=60,
		),
	)

	if cache is not None:
		try:
			cached_payload = {"_schema": "v9", **snap.__dict__}
			cache.set_value(key, cached_payload, expires_in_sec=60 * 5)
		except Exception:
			pass

	return snap


def _delete_aos_settings_cache() -> None:
	try:
		cache = frappe.cache()
	except Exception:
		return
	for key in (AOS_SETTINGS_CACHE_KEY,):
		try:
			cache.delete_value(key)
		except Exception:
			continue


def clear_aos_settings_cache() -> None:
	"""Invalidate now and post-commit so another node cannot retain stale settings."""
	_delete_aos_settings_cache()
	manager = getattr(frappe.db, "after_commit", None)
	if manager is not None and hasattr(manager, "add"):
		manager.add(_delete_aos_settings_cache)


def _parse_config_list(raw: str | None) -> list[str]:
	values: list[str] = []
	for line in str(raw or "").replace(",", "\n").splitlines():
		value = line.strip()
		if value:
			values.append(value)
	return list(dict.fromkeys(values))


def parse_google_oauth_client_ids(raw: str | None) -> list[str]:
	return _parse_config_list(raw)


def get_google_oauth_client_ids() -> list[str]:
	try:
		snap = get_aos_settings_snapshot()
	except Exception:
		return []
	return parse_google_oauth_client_ids(snap.google_oauth_client_ids)


def parse_apple_oauth_client_ids(raw: str | None) -> list[str]:
	return _parse_config_list(raw)


def get_apple_oauth_client_ids() -> list[str]:
	try:
		snap = get_aos_settings_snapshot()
	except Exception:
		return []
	return parse_apple_oauth_client_ids(snap.apple_oauth_client_ids)
