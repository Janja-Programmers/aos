"""Production-readiness configuration validation for AOS.

This module is intentionally read-only. It validates that deployment-time
configuration is explicit, non-placeholder, and safe enough for a production
release without exposing secret values in the returned report.

Bench usage:

    bench --site <site> execute aos.utils.production_config.validate_production_config
    bench --site <site> execute aos.utils.production_config.assert_production_config_ready
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import frappe

from aos.services.maps.internal_url import InvalidInternalMapsURL, normalize_internal_maps_url
from aos.utils import aos_config

MIN_SECRET_LENGTH = 24

_PLACEHOLDER_PATTERNS = (
	"change-this",
	"replace-with",
	"replace_",
	"replace-",
	"example.com",
	"example.org",
	"example.net",
	"your-domain",
	"your_",
	"todo",
	"dummy",
	"test-secret",
	"secret-key",
	"minioadmin",
	"password",
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1"}

_EXTERNAL_WORKER_SERVICES: tuple[dict[str, Any], ...] = (
	{
		"name": "video_processing",
		"category": "video",
		"service_url_keys": ("VIDEO_SERVICE_URL",),
		"service_secret_keys": ("VIDEO_SERVICE_SECRET",),
		"callback_secret_keys": ("VIDEO_SERVICE_CALLBACK_SECRET",),
		"callback_url_keys": ("VIDEO_CALLBACK_URL",),
		"callback_method": "aos.api.v1.video_processing.handle_callback",
		"enabled_keys": (),
		"enabled_default": True,
	},
	{
		"name": "moderation",
		"category": "moderation",
		"service_url_keys": ("MODERATION_SERVICE_URL",),
		"service_secret_keys": ("MODERATION_SERVICE_SECRET",),
		"callback_secret_keys": ("MODERATION_SERVICE_CALLBACK_SECRET",),
		"callback_url_keys": ("MODERATION_CALLBACK_URL",),
		"callback_method": "aos.api.v1.moderation.handle_callback",
		"enabled_keys": ("MODERATION_ENABLED",),
		"enabled_default": True,
	},
	{
		"name": "search_ranking",
		"category": "search_ranking",
		"service_url_keys": ("SEARCH_RANKING_SERVICE_URL",),
		"service_secret_keys": ("SEARCH_RANKING_SERVICE_SECRET",),
		"callback_secret_keys": ("SEARCH_RANKING_SERVICE_CALLBACK_SECRET",),
		"callback_url_keys": ("SEARCH_RANKING_CALLBACK_URL",),
		"callback_method": "aos.api.v1.search_ranking.handle_callback",
		"enabled_keys": ("SEARCH_RANKING_ENABLED",),
		"enabled_default": True,
	},
	{
		"name": "analytics_pipeline",
		"category": "analytics",
		"service_url_keys": ("ANALYTICS_SERVICE_URL",),
		"service_secret_keys": ("ANALYTICS_SERVICE_SECRET",),
		"callback_secret_keys": ("ANALYTICS_SERVICE_CALLBACK_SECRET",),
		"callback_url_keys": ("ANALYTICS_CALLBACK_URL",),
		"callback_method": "aos.api.v1.analytics_pipeline.handle_callback",
		"enabled_keys": ("ANALYTICS_PIPELINE_ENABLED",),
		"enabled_default": True,
	},
	{
		"name": "notification_delivery",
		"category": "notifications",
		"service_url_keys": ("NOTIFICATION_SERVICE_URL",),
		"service_secret_keys": ("NOTIFICATION_SERVICE_SECRET",),
		"callback_secret_keys": ("NOTIFICATION_SERVICE_CALLBACK_SECRET",),
		"callback_url_keys": ("NOTIFICATION_CALLBACK_URL",),
		"callback_method": "aos.api.v1.notifications.handle_delivery_callback",
		"enabled_keys": ("NOTIFICATION_DELIVERY_ENABLED",),
		"enabled_default": True,
	},
)

_WORKER_TIMEOUT_POLICY: dict[str, dict[str, Any]] = {
	"video_processing": {
		"job_timeout_key": "VIDEO_JOB_TIMEOUT_SECONDS",
		"job_timeout_default": 1800,
		"callback_timeout_key": "AOS_VIDEO_CALLBACK_TIMEOUT_SECONDS",
		"callback_timeout_default": 2700,
	},
	"moderation": {
		"job_timeout_key": "MODERATION_JOB_TIMEOUT_SECONDS",
		"job_timeout_default": 600,
		"callback_timeout_key": "AOS_MODERATION_CALLBACK_TIMEOUT_SECONDS",
		"callback_timeout_default": 1200,
	},
	"search_ranking": {
		"job_timeout_key": "SEARCH_RANKING_JOB_TIMEOUT_SECONDS",
		"job_timeout_default": 600,
		"callback_timeout_key": "AOS_SEARCH_CALLBACK_TIMEOUT_SECONDS",
		"callback_timeout_default": 1200,
	},
	"notification_delivery": {
		"job_timeout_key": "NOTIFICATION_JOB_TIMEOUT_SECONDS",
		"job_timeout_default": 600,
		"callback_timeout_key": "AOS_NOTIFICATION_CALLBACK_TIMEOUT_SECONDS",
		"callback_timeout_default": 1200,
	},
	"analytics_pipeline": {
		"job_timeout_key": "ANALYTICS_JOB_TIMEOUT_SECONDS",
		"job_timeout_default": 600,
		"callback_timeout_key": "AOS_ANALYTICS_CALLBACK_TIMEOUT_SECONDS",
		"callback_timeout_default": 1200,
	},
}
_WORKER_CALLBACK_RETRY_MARGIN_SECONDS = 300


_PRIVATE_SERVICE_URLS: tuple[dict[str, Any], ...] = (
	{
		"category": "ai_ml",
		"name": "translation",
		"keys": ("TRANSLATION_SERVICE_URL",),
	},
	{
		"category": "ai_ml",
		"name": "image_search",
		"keys": ("IMAGE_SEARCH_SERVICE_URL",),
	},
	{
		"category": "ai_ml",
		"name": "background_removal",
		"keys": ("BACKGROUND_REMOVAL_SERVICE_URL",),
	},
)


class ProductionConfigError(RuntimeError):
	"""Raised when production configuration validation fails."""


def _clean(value: Any) -> str:
	return str(value or "").strip()


def _env_value(env: Mapping[str, Any] | None, *keys: str) -> tuple[str, str | None]:
	for key in keys:
		if env is not None:
			value = _clean(env.get(key))
		else:
			value = _clean(aos_config.get_env(key))
		if value:
			return value, key
	return "", None


def _site_value(site_config: Mapping[str, Any] | None, key: str) -> str:
	if site_config is not None:
		return _clean(site_config.get(key))
	try:
		return _clean(frappe.conf.get(key))
	except Exception:
		return ""


def _bool_env(env: Mapping[str, Any] | None, keys: tuple[str, ...], default: bool) -> bool:
	value, _ = _env_value(env, *keys)
	if not value:
		return bool(default)
	return value.lower() in {"1", "true", "yes", "y", "on"}



def _bounded_int_env(
	env: Mapping[str, Any] | None,
	key: str,
	default: int,
	*,
	minimum: int = 1,
	maximum: int = 86400,
) -> tuple[int, bool]:
	value, _ = _env_value(env, key)
	if not value:
		return default, True
	try:
		parsed = int(value)
	except (TypeError, ValueError):
		return default, False
	return max(minimum, min(parsed, maximum)), minimum <= parsed <= maximum

def _is_placeholder(value: str) -> bool:
	lowered = _clean(value).lower()
	if not lowered:
		return False
	return any(pattern in lowered for pattern in _PLACEHOLDER_PATTERNS)


def _is_probably_secret_placeholder(value: str) -> bool:
	lowered = _clean(value).lower()
	if not lowered:
		return False
	if _is_placeholder(lowered):
		return True
	if lowered in {"secret", "password", "changeme", "admin", "123456", "12345678"}:
		return True
	return False


def _host(url: str) -> str:
	parsed = urlparse(url if "://" in url else f"//{url}")
	return (parsed.hostname or "").strip().lower()


def _is_local_url(url: str) -> bool:
	host = _host(url)
	return host in _LOCAL_HOSTS


def _is_valid_url(url: str, *, require_scheme: bool = True) -> bool:
	value = _clean(url)
	if not value:
		return False
	parsed = urlparse(value)
	if require_scheme and parsed.scheme not in {"http", "https", "ws", "wss"}:
		return False
	if require_scheme and not parsed.netloc:
		return False
	return True


def _public_url_is_safe(url: str, *, require_https: bool = True) -> bool:
	value = _clean(url)
	if not _is_valid_url(value):
		return False
	parsed = urlparse(value)
	if require_https and parsed.scheme not in {"https", "wss"}:
		return False
	if _is_local_url(value):
		return False
	if _is_placeholder(value):
		return False
	return True


def _redacted_issue(
	issues: list[dict[str, Any]],
	*,
	severity: str,
	category: str,
	key: str,
	message: str,
	remediation: str,
) -> None:
	issues.append(
		{
			"severity": severity,
			"category": category,
			"key": key,
			"message": message,
			"remediation": remediation,
		}
	)


def _check_required_value(
	issues: list[dict[str, Any]],
	*,
	env: Mapping[str, Any] | None,
	category: str,
	keys: tuple[str, ...],
	label: str,
	secret: bool = False,
	min_length: int = MIN_SECRET_LENGTH,
) -> str:
	value, source_key = _env_value(env, *keys)
	display_key = source_key or "/".join(keys)

	if not value:
		_redacted_issue(
			issues,
			severity="error",
			category=category,
			key=display_key,
			message=f"{label} is missing.",
			remediation=f"Set {display_key} to a production value.",
		)
		return ""

	if secret:
		if len(value) < min_length:
			_redacted_issue(
				issues,
				severity="error",
				category=category,
				key=display_key,
				message=f"{label} is too short for production.",
				remediation=f"Set {display_key} to a long random value of at least {min_length} characters.",
			)
		if _is_probably_secret_placeholder(value):
			_redacted_issue(
				issues,
				severity="error",
				category=category,
				key=display_key,
				message=f"{label} still looks like a placeholder/default value.",
				remediation=f"Rotate {display_key} to a unique production secret.",
			)
	elif _is_placeholder(value):
		_redacted_issue(
			issues,
			severity="error",
			category=category,
			key=display_key,
			message=f"{label} still looks like a placeholder/default value.",
			remediation=f"Set {display_key} to a real production value.",
		)

	return value


def _check_required_private_url(
	issues: list[dict[str, Any]],
	*,
	env: Mapping[str, Any] | None,
	category: str,
	keys: tuple[str, ...],
	label: str,
) -> str:
	value = _check_required_value(
		issues,
		env=env,
		category=category,
		keys=keys,
		label=label,
	)
	display_key = _env_value(env, *keys)[1] or "/".join(keys)
	if value and not _is_valid_url(value):
		_redacted_issue(
			issues,
			severity="error",
			category=category,
			key=display_key,
			message=f"{label} must be an HTTP(S) URL.",
			remediation=f"Set {display_key} to a valid internal service URL.",
		)
	return value


def _check_required_public_url(
	issues: list[dict[str, Any]],
	*,
	env: Mapping[str, Any] | None,
	category: str,
	keys: tuple[str, ...],
	label: str,
	require_https: bool = True,
) -> str:
	value = _check_required_value(
		issues,
		env=env,
		category=category,
		keys=keys,
		label=label,
	)
	display_key = _env_value(env, *keys)[1] or "/".join(keys)
	if value and not _public_url_is_safe(value, require_https=require_https):
		scheme_text = "HTTPS/WSS" if require_https else "HTTP(S)"
		_redacted_issue(
			issues,
			severity="error",
			category=category,
			key=display_key,
			message=f"{label} must be a real public {scheme_text} URL/domain and must not be localhost or an example value.",
			remediation=f"Set {display_key} to the production public endpoint.",
		)
	return value


def _callback_url_or_domain(
	issues: list[dict[str, Any]],
	*,
	env: Mapping[str, Any] | None,
	category: str,
	service_name: str,
	callback_url_keys: tuple[str, ...],
	callback_method: str,
) -> None:
	callback_url, callback_key = _env_value(env, *callback_url_keys)
	if callback_url:
		key = callback_key or "/".join(callback_url_keys)
		if callback_method not in callback_url:
			_redacted_issue(
				issues,
				severity="warning",
				category=category,
				key=key,
				message=f"{service_name} callback URL does not contain the expected callback method path.",
				remediation=f"Confirm {key} points to {callback_method}.",
			)
		if not _public_url_is_safe(callback_url, require_https=True):
			_redacted_issue(
				issues,
				severity="error",
				category=category,
				key=key,
				message=f"{service_name} callback URL must be a real public HTTPS URL.",
				remediation=f"Set {key} to the production callback URL.",
			)
		return

	domain, domain_key = _env_value(env, "AOS_API_DOMAIN")
	if not domain:
		_redacted_issue(
			issues,
			severity="error",
			category=category,
			key="/".join((*callback_url_keys, "AOS_API_DOMAIN")),
			message=f"{service_name} callback URL cannot be derived because no callback URL or AOS API domain is configured.",
			remediation=f"Set {callback_url_keys[0]} or AOS_API_DOMAIN.",
		)
		return

	domain_url = f"https://{domain}"
	if not _public_url_is_safe(domain_url, require_https=True):
		_redacted_issue(
			issues,
			severity="error",
			category=category,
			key=domain_key or "AOS_API_DOMAIN",
			message=f"AOS API domain used to derive {service_name} callback URL is not production-safe.",
			remediation="Set AOS_API_DOMAIN to the real production API domain.",
		)


def _check_worker_services(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	for service in _EXTERNAL_WORKER_SERVICES:
		enabled = _bool_env(
			env,
			tuple(service["enabled_keys"]),
			bool(service["enabled_default"]),
		)
		if not enabled:
			_redacted_issue(
				issues,
				severity="warning",
				category=str(service["category"]),
				key=str(service["enabled_keys"][0]) if service["enabled_keys"] else str(service["name"]),
				message=f"{service['name']} is disabled.",
				remediation="Confirm this is intentional before production release.",
			)
			continue

		_check_required_private_url(
			issues,
			env=env,
			category=str(service["category"]),
			keys=tuple(service["service_url_keys"]),
			label=f"{service['name']} service URL",
		)
		_check_required_value(
			issues,
			env=env,
			category=str(service["category"]),
			keys=tuple(service["service_secret_keys"]),
			label=f"{service['name']} dispatch secret",
			secret=True,
		)
		_check_required_value(
			issues,
			env=env,
			category=str(service["category"]),
			keys=tuple(service["callback_secret_keys"]),
			label=f"{service['name']} callback secret",
			secret=True,
		)
		_callback_url_or_domain(
			issues,
			env=env,
			category=str(service["category"]),
			service_name=str(service["name"]),
			callback_url_keys=tuple(service["callback_url_keys"]),
			callback_method=str(service["callback_method"]),
		)

		policy = _WORKER_TIMEOUT_POLICY.get(str(service["name"]), {})
		job_key = str(policy.get("job_timeout_key") or "")
		callback_key = str(policy.get("callback_timeout_key") or "")
		job_timeout, job_valid = _bounded_int_env(
			env, job_key, int(policy.get("job_timeout_default") or 600), minimum=60, maximum=43200
		)
		callback_timeout, callback_valid = _bounded_int_env(
			env, callback_key, int(policy.get("callback_timeout_default") or 1200), minimum=120, maximum=86400
		)
		if not job_valid:
			_redacted_issue(
				issues,
				severity="error",
				category=str(service["category"]),
				key=job_key,
				message=f"{service['name']} work timeout must be an integer between 60 and 43200 seconds.",
				remediation=f"Set {job_key} to the reviewed maximum work runtime.",
			)
		if not callback_valid:
			_redacted_issue(
				issues,
				severity="error",
				category=str(service["category"]),
				key=callback_key,
				message=f"{service['name']} callback observation timeout must be an integer between 120 and 86400 seconds.",
				remediation=f"Set {callback_key} to a reviewed service-specific timeout.",
			)
		minimum_callback_timeout = job_timeout + _WORKER_CALLBACK_RETRY_MARGIN_SECONDS
		if callback_timeout <= minimum_callback_timeout:
			_redacted_issue(
				issues,
				severity="error",
				category=str(service["category"]),
				key=callback_key,
				message=(
					f"{service['name']} callback observation timeout must exceed the maximum work runtime "
					"plus the callback retry margin."
				),
				remediation=(
					f"Set {callback_key} above {job_key} by at least "
					f"{_WORKER_CALLBACK_RETRY_MARGIN_SECONDS} seconds."
				),
			)


def _check_storage(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	# Media owns a provider-neutral S3-compatible boundary. These values are
	# deployment identity/secrets and intentionally never come from AOS Settings.
	media_endpoint = _check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("AOS_OBJECT_STORAGE_ENDPOINT",),
		label="Media object-storage endpoint",
	)
	if media_endpoint:
		try:
			aos_config.clean_endpoint(
				media_endpoint, setting_name="AOS_OBJECT_STORAGE_ENDPOINT"
			)
		except Exception:
			_redacted_issue(
				issues,
				severity="error",
				category="storage",
				key="AOS_OBJECT_STORAGE_ENDPOINT",
				message="Media object-storage endpoint must be host[:port] without scheme, bucket, query, or path.",
				remediation="Set AOS_OBJECT_STORAGE_ENDPOINT to the deployment's S3-compatible API endpoint.",
			)

	_check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("AOS_OBJECT_STORAGE_ACCESS_KEY",),
		label="Media object-storage access key",
		secret=True,
		min_length=8,
	)
	_check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("AOS_OBJECT_STORAGE_SECRET_KEY",),
		label="Media object-storage secret key",
		secret=True,
	)
	secure_value = _check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("AOS_OBJECT_STORAGE_SECURE",),
		label="Media object-storage TLS setting",
	)
	if secure_value and not _bool_env(env, ("AOS_OBJECT_STORAGE_SECURE",), False):
		_redacted_issue(
			issues,
			severity="error",
			category="storage",
			key="AOS_OBJECT_STORAGE_SECURE",
			message="Production Media object-storage API connections must use TLS.",
			remediation="Set AOS_OBJECT_STORAGE_SECURE=true for the production S3-compatible endpoint.",
		)
	path_style_value = _check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("AOS_OBJECT_STORAGE_PATH_STYLE",),
		label="Media object-storage addressing style",
	)
	if path_style_value and path_style_value.strip().lower() not in {
		"1", "0", "true", "false", "yes", "no", "on", "off",
	}:
		_redacted_issue(
			issues,
			severity="error",
			category="storage",
			key="AOS_OBJECT_STORAGE_PATH_STYLE",
			message="Media object-storage addressing style must be an explicit boolean.",
			remediation="Use false for virtual-host-style providers such as Hetzner/AWS, or true where path-style is required.",
		)
	_check_required_public_url(
		issues,
		env=env,
		category="storage",
		keys=("AOS_OBJECT_STORAGE_PRESIGN_ENDPOINT",),
		label="Media presigned-upload origin",
		require_https=True,
	)
	_check_required_public_url(
		issues,
		env=env,
		category="storage",
		keys=("AOS_MEDIA_PUBLIC_BASE_URL",),
		label="Media public delivery base URL",
		require_https=True,
	)
	media_buckets: list[str] = []
	for key, label in (
		("AOS_OBJECT_STORAGE_PUBLIC_BUCKET", "Media public bucket"),
		("AOS_OBJECT_STORAGE_PRIVATE_BUCKET", "Media private bucket"),
	):
		value = _check_required_value(
			issues, env=env, category="storage", keys=(key,), label=label
		)
		if value:
			media_buckets.append(value)
		if value and any(char in value for char in ("/", "\\")):
			_redacted_issue(
				issues,
				severity="error",
				category="storage",
				key=key,
				message=f"{label} must be a bucket name, not a path.",
				remediation=f"Set {key} to one deployment-controlled bucket name.",
			)
	if len(media_buckets) == 2 and media_buckets[0] == media_buckets[1]:
		_redacted_issue(
			issues,
			severity="error",
			category="storage",
			key="AOS_OBJECT_STORAGE_PUBLIC_BUCKET/AOS_OBJECT_STORAGE_PRIVATE_BUCKET",
			message="Media public and private buckets must be different.",
			remediation="Use separate buckets so public delivery policy cannot expose private media.",
		)
	if _bool_env(env, ("AOS_OBJECT_STORAGE_MANAGE_BUCKETS",), False):
		_redacted_issue(
			issues,
			severity="error",
			category="storage",
			key="AOS_OBJECT_STORAGE_MANAGE_BUCKETS",
			message="Production Media must not mutate bucket creation/public-read policy at runtime.",
			remediation="Provision buckets and bucket/CDN policy through infrastructure, then set AOS_OBJECT_STORAGE_MANAGE_BUCKETS=false.",
		)

	# Legacy MinIO validation remains because non-Media Shorts/video components
	# still depend on it and are explicitly outside this hardening pass.
	endpoint = _check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("MINIO_ENDPOINT", "AOS_MINIO_ENDPOINT"),
		label="MinIO endpoint",
	)
	if endpoint:
		try:
			aos_config.clean_endpoint(endpoint)
		except Exception:
			_redacted_issue(
				issues,
				severity="error",
				category="storage",
				key=_env_value(env, "MINIO_ENDPOINT", "AOS_MINIO_ENDPOINT")[1]
				or "MINIO_ENDPOINT/AOS_MINIO_ENDPOINT",
				message="MinIO endpoint must be host[:port] without scheme, bucket, query, or path.",
				remediation="Move public URL/path settings to MINIO_PUBLIC_BASE_URL and keep MINIO_ENDPOINT as host[:port].",
			)

	_check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("MINIO_ACCESS_KEY", "MINIO_ROOT_USER"),
		label="MinIO access key",
		secret=True,
		min_length=8,
	)
	_check_required_value(
		issues,
		env=env,
		category="storage",
		keys=("MINIO_SECRET_KEY", "MINIO_ROOT_PASSWORD"),
		label="MinIO secret key",
		secret=True,
	)
	_check_required_public_url(
		issues,
		env=env,
		category="storage",
		keys=("MINIO_PUBLIC_BASE_URL", "MINIO_PUBLIC_URL", "AOS_MINIO_PUBLIC_BASE_URL"),
		label="MinIO public base URL",
		require_https=True,
	)
	for key, label in (
		("AOS_PUBLIC_BUCKET", "public media bucket"),
		("AOS_PRIVATE_BUCKET", "private media bucket"),
		("AOS_MINIO_BUCKET", "Shorts output bucket"),
	):
		value = _check_required_value(
			issues,
			env=env,
			category="storage",
			keys=(key,),
			label=label,
		)
		if value and "/" in value:
			_redacted_issue(
				issues,
				severity="error",
				category="storage",
				key=key,
				message=f"{label} must be a bucket name, not a path.",
				remediation=f"Remove slashes from {key} and use AOS_MINIO_BASE_PATH for paths.",
			)

	for key, default, minimum, maximum, label in (
		("AOS_STORAGE_CONNECT_TIMEOUT_SECONDS", 3, 1, 30, "storage connect timeout"),
		("AOS_STORAGE_READ_TIMEOUT_SECONDS", 15, 1, 300, "storage read timeout"),
		("AOS_STORAGE_MAX_RETRIES", 2, 0, 5, "storage retry count"),
		("AOS_STORAGE_RETRY_BACKOFF_MS", 200, 0, 5000, "storage retry backoff"),
		("AOS_MEDIA_DOWNLOAD_EXPIRY_MINUTES", 10, 1, 60, "private download expiry"),
		("AOS_MEDIA_UNATTACHED_RETENTION_DAYS", 7, 1, 90, "unattached retention"),
		("AOS_MEDIA_DELETE_RETRY_HOURS", 1, 1, 24, "delete retry interval"),
		("AOS_MEDIA_CLEANUP_BATCH_LIMIT", 100, 10, 1000, "cleanup batch limit"),
	):
		_value, valid = _bounded_int_env(env, key, default, minimum=minimum, maximum=maximum)
		if not valid:
			_redacted_issue(
				issues,
				severity="error",
				category="storage",
				key=key,
				message=f"{label} must be an integer between {minimum} and {maximum}.",
				remediation=f"Set {key} to a reviewed bounded value.",
			)


def _check_livekit(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	endpoint, endpoint_key = _env_value(env, "LIVEKIT_ENDPOINT", "AOS_LIVEKIT_ENDPOINT")
	if not endpoint:
		domain, domain_key = _env_value(env, "AOS_LIVEKIT_DOMAIN")
		if domain:
			endpoint = f"wss://{domain}"
			endpoint_key = domain_key
	if not endpoint:
		_redacted_issue(
			issues,
			severity="error",
			category="livekit",
			key="LIVEKIT_ENDPOINT/AOS_LIVEKIT_ENDPOINT/AOS_LIVEKIT_DOMAIN",
			message="LiveKit public endpoint is missing.",
			remediation="Set LIVEKIT_ENDPOINT to the production WSS endpoint or set AOS_LIVEKIT_DOMAIN.",
		)
	elif not _public_url_is_safe(endpoint, require_https=True) or urlparse(endpoint).scheme != "wss":
		_redacted_issue(
			issues,
			severity="error",
			category="livekit",
			key=endpoint_key or "LIVEKIT_ENDPOINT",
			message="LiveKit endpoint must be a real public WSS URL.",
			remediation="Set LIVEKIT_ENDPOINT to wss://<production-livekit-domain>.",
		)

	livekit_keys, _ = _env_value(env, "LIVEKIT_KEYS")
	api_key, _ = _env_value(env, "LIVEKIT_API_KEY")
	api_secret, _ = _env_value(env, "LIVEKIT_API_SECRET")

	if livekit_keys and ":" in livekit_keys:
		split_key, split_secret = livekit_keys.split(":", 1)
		api_key = api_key or split_key.strip()
		api_secret = api_secret or split_secret.strip()

	if not api_key:
		_redacted_issue(
			issues,
			severity="error",
			category="livekit",
			key="LIVEKIT_API_KEY/LIVEKIT_KEYS",
			message="LiveKit API key is missing.",
			remediation="Set LIVEKIT_API_KEY or LIVEKIT_KEYS.",
		)
	elif _is_placeholder(api_key):
		_redacted_issue(
			issues,
			severity="error",
			category="livekit",
			key="LIVEKIT_API_KEY/LIVEKIT_KEYS",
			message="LiveKit API key still looks like a placeholder/default value.",
			remediation="Set a real production LiveKit API key.",
		)

	if not api_secret:
		_redacted_issue(
			issues,
			severity="error",
			category="livekit",
			key="LIVEKIT_API_SECRET/LIVEKIT_KEYS",
			message="LiveKit API secret is missing.",
			remediation="Set LIVEKIT_API_SECRET or LIVEKIT_KEYS.",
		)
	elif len(api_secret) < MIN_SECRET_LENGTH or _is_probably_secret_placeholder(api_secret):
		_redacted_issue(
			issues,
			severity="error",
			category="livekit",
			key="LIVEKIT_API_SECRET/LIVEKIT_KEYS",
			message="LiveKit API secret is too weak or still looks like a placeholder/default value.",
			remediation="Set a long random production LiveKit API secret.",
		)


def _check_public_domains(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	for key, label in (
		("AOS_API_DOMAIN", "AOS API domain"),
		("AOS_MAPS_DOMAIN", "AOS maps domain"),
		("AOS_MINIO_DOMAIN", "AOS files domain"),
	):
		value = _check_required_value(
			issues,
			env=env,
			category="domains",
			keys=(key,),
			label=label,
		)
		if value:
			url_value = value if "://" in value else f"https://{value}"
			if not _public_url_is_safe(url_value, require_https=True):
				_redacted_issue(
					issues,
					severity="error",
					category="domains",
					key=key,
					message=f"{label} must be a real production domain, not localhost or an example value.",
					remediation=f"Set {key} to the real production domain.",
				)


def _check_firebase(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	notification_enabled = _bool_env(env, ("NOTIFICATION_DELIVERY_ENABLED",), True)
	dry_run = _bool_env(env, ("NOTIFICATION_DRY_RUN",), False)
	if notification_enabled and dry_run:
		_redacted_issue(
			issues,
			severity="error",
			category="notifications",
			key="NOTIFICATION_DRY_RUN",
			message="Notification dry-run mode is enabled.",
			remediation="Set NOTIFICATION_DRY_RUN=false before production release.",
		)

	if not notification_enabled and _bool_env(env, ("NOTIFICATION_WEB_PUSH_ENABLED",), False):
		_redacted_issue(
			issues,
			severity="error",
			category="notifications",
			key="NOTIFICATION_WEB_PUSH_ENABLED",
			message="Firebase Web Messaging cannot be enabled while Notification delivery is disabled.",
			remediation="Enable Notification delivery or disable web push.",
		)

	if not notification_enabled:
		_redacted_issue(
			issues,
			severity="warning",
			category="notifications",
			key="NOTIFICATION_DELIVERY_ENABLED",
			message="Notification delivery is disabled.",
			remediation="Confirm this is intentional before production release.",
		)
		return

	value, key = _env_value(
		env,
		"NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
		"FIREBASE_SERVICE_ACCOUNT_PATH",
	)
	if not value:
		value, key = _env_value(env, "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH")
	if not value:
		_redacted_issue(
			issues,
			severity="error",
			category="notifications",
			key="NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
			message="Firebase service account path is missing.",
			remediation="Set NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH for the worker container or NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH for deployment checks.",
		)
	elif _is_placeholder(value):
		_redacted_issue(
			issues,
			severity="error",
			category="notifications",
			key=key or "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
			message="Firebase service account path still looks like a placeholder/default value.",
			remediation="Set the Firebase service account path to the production secret file location.",
		)

	if _bool_env(env, ("NOTIFICATION_WEB_PUSH_ENABLED",), False):
		required_web_push = (
			"NOTIFICATION_FIREBASE_WEB_API_KEY",
			"NOTIFICATION_FIREBASE_WEB_PROJECT_ID",
			"NOTIFICATION_FIREBASE_WEB_MESSAGING_SENDER_ID",
			"NOTIFICATION_FIREBASE_WEB_APP_ID",
			"NOTIFICATION_FIREBASE_WEB_VAPID_PUBLIC_KEY",
		)
		for web_key in required_web_push:
			web_value, _ = _env_value(env, web_key)
			if not web_value or _is_placeholder(web_value):
				_redacted_issue(
					issues,
					severity="error",
					category="notifications",
					key=web_key,
					message="Firebase Web Messaging is enabled but required public bootstrap configuration is missing or placeholder.",
					remediation=f"Set {web_key} to the production Firebase Web Messaging public value.",
				)


def _check_ai_services(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	for service in _PRIVATE_SERVICE_URLS:
		_check_required_private_url(
			issues,
			env=env,
			category=str(service["category"]),
			keys=tuple(service["keys"]),
			label=f"{service['name']} service URL",
		)

	_check_required_value(
		issues,
		env=env,
		category="ai_ml",
		keys=("BACKGROUND_REMOVAL_SERVICE_SECRET",),
		label="background-removal internal service secret",
		secret=True,
	)

	qdrant_url = _check_required_private_url(
		issues,
		env=env,
		category="ai_ml",
		keys=("IMAGE_SEARCH_QDRANT_URL", "QDRANT_URL"),
		label="image-search Qdrant URL",
	)
	if qdrant_url and _is_placeholder(qdrant_url):
		_redacted_issue(
			issues,
			severity="error",
			category="ai_ml",
			key="IMAGE_SEARCH_QDRANT_URL/QDRANT_URL",
			message="Qdrant URL still looks like a placeholder/default value.",
			remediation="Set the image-search Qdrant URL to the production internal Qdrant endpoint.",
		)

	_check_required_value(
		issues,
		env=env,
		category="ai_ml",
		keys=("SHORT_CLASSIFICATION_SECRET", "IMAGE_SEARCH_INTERNAL_SECRET"),
		label="image-search internal request secret",
		secret=True,
	)

	allowed_hosts, allowed_hosts_key = _env_value(env, "IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS")
	file_base_url, file_base_key = _env_value(env, "IMAGE_SEARCH_FILE_BASE_URL")
	media_public_url, _ = _env_value(env, "AOS_MEDIA_PUBLIC_BASE_URL")
	if not allowed_hosts and not file_base_url and not media_public_url:
		_redacted_issue(
			issues,
			severity="error",
			category="ai_ml",
			key="IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS/IMAGE_SEARCH_FILE_BASE_URL/AOS_MEDIA_PUBLIC_BASE_URL",
			message="Image-search remote image fetching has no trusted host configured.",
			remediation="Set the canonical AOS_MEDIA_PUBLIC_BASE_URL or configure IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS/IMAGE_SEARCH_FILE_BASE_URL.",
		)
	if file_base_url and not _public_url_is_safe(file_base_url, require_https=True):
		_redacted_issue(
			issues,
			severity="error",
			category="ai_ml",
			key=file_base_key or "IMAGE_SEARCH_FILE_BASE_URL",
			message="Image-search file base URL must be a real public HTTPS URL.",
			remediation="Set IMAGE_SEARCH_FILE_BASE_URL to the production public media/API HTTPS origin.",
		)
	if allowed_hosts:
		invalid_hosts = [
			host
			for host in (item.strip() for item in allowed_hosts.split(","))
			if host and ("://" in host or "/" in host or "@" in host or "*" in host)
		]
		if invalid_hosts:
			_redacted_issue(
				issues,
				severity="error",
				category="ai_ml",
				key=allowed_hosts_key or "IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS",
				message="Image-search allowed hosts must contain exact hostnames only, without schemes, paths, credentials, or wildcards.",
				remediation="Set IMAGE_SEARCH_ALLOWED_IMAGE_HOSTS to a comma-separated list of exact trusted hostnames.",
			)


def _check_maps(
	issues: list[dict[str, Any]],
	env: Mapping[str, Any] | None,
	site_config: Mapping[str, Any] | None,
) -> None:
	_check_required_public_url(
		issues,
		env=env,
		category="maps",
		keys=("MAPS_PUBLIC_BASE_URL",),
		label="Maps basemap/CDN public URL",
		require_https=True,
	)

	def require_internal_url(key: str, name: str) -> None:
		value = _site_value(site_config, key)
		valid = False
		if value and not _is_placeholder(value):
			try:
				normalize_internal_maps_url(value, service=name)
			except InvalidInternalMapsURL:
				pass
			else:
				valid = True
		if not valid:
			_redacted_issue(
				issues,
				severity="error",
				category="maps",
				key=key,
				message=f"{name} is missing, invalid, unsafe, or still a placeholder.",
				remediation=f"Set {key} to a configured internal HTTP(S) endpoint; loopback is valid for a per-host sidecar deployment.",
			)

	# Photon is the canonical production geocoder; it is not optional in a
	# production-ready Maps deployment. Its external OpenSearch cluster remains
	# an infrastructure concern and is never client-visible.
	photon_enabled = _site_value(site_config, "maps_photon_enabled").lower() in {"1", "true", "yes", "on"}
	if not photon_enabled:
		_redacted_issue(
			issues,
			severity="error",
			category="maps",
			key="maps_photon_enabled",
			message="Photon must be enabled for production Maps geocoding.",
			remediation="Set maps_photon_enabled to 1 after deploying Photon against external OpenSearch.",
		)
	require_internal_url("photon_base_url", "Photon")

	nominatim_fallback = _site_value(site_config, "maps_nominatim_fallback_enabled").lower() in {"1", "true", "yes", "on"}
	if nominatim_fallback:
		require_internal_url("nominatim_base_url", "Nominatim fallback")

	routing_enabled = _site_value(site_config, "maps_routing_enabled").lower() in {"1", "true", "yes", "on"}
	if not routing_enabled:
		_redacted_issue(
			issues,
			severity="error",
			category="maps",
			key="maps_routing_enabled",
			message="Valhalla routing must be enabled for production Maps.",
			remediation="Deploy a verified global Valhalla graph/runtime, set maps_routing_enabled to 1, and configure valhalla_base_url to its private endpoint.",
		)
	require_internal_url("valhalla_base_url", "Valhalla")


def _check_environment_and_secret_sources(
	issues: list[dict[str, Any]], env: Mapping[str, Any] | None
) -> None:
	environment, _ = _env_value(env, "AOS_ENVIRONMENT", "ENVIRONMENT")
	if environment.lower() != "production":
		_redacted_issue(
			issues,
			severity="error",
			category="environment",
			key="AOS_ENVIRONMENT",
			message="Production configuration validation requires AOS_ENVIRONMENT=production.",
			remediation="Set AOS_ENVIRONMENT=production only on production hosts and use development/staging elsewhere.",
		)
	env_path, env_key = _env_value(env, "AOS_ENV_FILE_PATH", "ENV_FILE")
	if env_path and ("example" in Path(env_path).name.lower() or env_path.endswith(".dist")):
		_redacted_issue(
			issues,
			severity="error",
			category="secrets",
			key=env_key or "AOS_ENV_FILE_PATH",
			message="An example environment file appears to be mounted as production configuration.",
			remediation="Mount a root-owned production secret file, never an example or distribution file.",
		)


def _check_backup_encryption_and_alerting(
	issues: list[dict[str, Any]], env: Mapping[str, Any] | None
) -> None:
	required = _bool_env(env, ("BACKUP_ENCRYPTION_REQUIRED",), False)
	method, _ = _env_value(env, "BACKUP_ENCRYPTION_METHOD")
	recipient, _ = _env_value(env, "BACKUP_AGE_RECIPIENT")
	if not required:
		_redacted_issue(
			issues,
			severity="error",
			category="backup",
			key="BACKUP_ENCRYPTION_REQUIRED",
			message="Production backup encryption is not required by configuration.",
			remediation="Set BACKUP_ENCRYPTION_REQUIRED=true and configure a verified age recipient.",
		)
	if method.lower() != "age":
		_redacted_issue(
			issues,
			severity="error",
			category="backup",
			key="BACKUP_ENCRYPTION_METHOD",
			message="Production backups must use the supported age encryption method.",
			remediation="Set BACKUP_ENCRYPTION_METHOD=age.",
		)
	if not recipient or _is_placeholder(recipient) or recipient.lower().startswith("age1example"):
		_redacted_issue(
			issues,
			severity="error",
			category="backup",
			key="BACKUP_AGE_RECIPIENT",
			message="Backup age recipient is missing or a placeholder.",
			remediation="Set BACKUP_AGE_RECIPIENT to the real public age recipient; keep the identity outside the repository.",
		)
	local_mode, _ = _env_value(env, "BACKUP_LOCAL_RETENTION_MODE")
	if local_mode.lower() != "encrypted-artifact":
		_redacted_issue(
			issues,
			severity="error",
			category="backup",
			key="BACKUP_LOCAL_RETENTION_MODE",
			message="Production local backup retention is not encrypted-only.",
			remediation="Set BACKUP_LOCAL_RETENTION_MODE=encrypted-artifact so plaintext exists only in the restricted temporary workspace.",
		)

	metrics_token, _ = _env_value(env, "AOS_METRICS_TOKEN")
	if (
		not metrics_token
		or len(metrics_token) < MIN_SECRET_LENGTH
		or _is_probably_secret_placeholder(metrics_token)
	):
		_redacted_issue(
			issues,
			severity="error",
			category="monitoring",
			key="AOS_METRICS_TOKEN",
			message="Private metrics authentication is missing or unsafe.",
			remediation="Set AOS_METRICS_TOKEN from the production secret manager with at least 24 random characters.",
		)
	if _bool_env(env, ("AOS_METRICS_ALLOW_PROCESS_FALLBACK",), False):
		_redacted_issue(
			issues,
			severity="error",
			category="monitoring",
			key="AOS_METRICS_ALLOW_PROCESS_FALLBACK",
			message="Process-local Frappe metrics fallback is unsafe in a multi-worker production deployment.",
			remediation="Set AOS_METRICS_ALLOW_PROCESS_FALLBACK=false and ensure Redis metrics aggregation is available.",
		)
	alerting_enabled = _bool_env(env, ("AOS_ALERTING_ENABLED",), False)
	if not alerting_enabled:
		_redacted_issue(
			issues,
			severity="error",
			category="monitoring",
			key="AOS_ALERTING_ENABLED",
			message="Production alerting is disabled.",
			remediation="Set AOS_ALERTING_ENABLED=true after configuring Prometheus and Alertmanager routing.",
		)
	failure_method, _ = _env_value(env, "BACKUP_FAILURE_NOTIFICATION_METHOD")
	if failure_method.lower() not in {"webhook", "email"}:
		_redacted_issue(
			issues,
			severity="error",
			category="monitoring",
			key="BACKUP_FAILURE_NOTIFICATION_METHOD",
			message="Backup systemd failure notification is not configured.",
			remediation="Configure webhook or email failure notification for the backup unit.",
		)
	if failure_method.lower() == "webhook":
		webhook, _ = _env_value(env, "BACKUP_FAILURE_WEBHOOK_URL")
		if not webhook or _is_placeholder(webhook):
			_redacted_issue(
				issues,
				severity="error",
				category="monitoring",
				key="BACKUP_FAILURE_WEBHOOK_URL",
				message="Backup failure webhook is missing or a placeholder.",
				remediation="Store the real operator webhook in the production secret source.",
			)
	if failure_method.lower() == "email":
		recipient_email, _ = _env_value(env, "BACKUP_FAILURE_EMAIL_TO")
		if not recipient_email or _is_placeholder(recipient_email):
			_redacted_issue(
				issues,
				severity="error",
				category="monitoring",
				key="BACKUP_FAILURE_EMAIL_TO",
				message="Backup failure email recipient is missing or a placeholder.",
				remediation="Set the operator distribution address in production configuration.",
			)

	if _bool_env(env, ("AOS_CD_ENABLED",), False):
		manifest_required = _bool_env(env, ("AOS_RELEASE_MANIFEST_REQUIRED",), False)
		if not manifest_required:
			_redacted_issue(
				issues,
				severity="error",
				category="deployment",
				key="AOS_RELEASE_MANIFEST_REQUIRED",
				message="Controlled deployment is enabled without immutable release-manifest enforcement.",
				remediation="Set AOS_RELEASE_MANIFEST_REQUIRED=true and deploy only exact commits and image digests.",
			)
		for key in ("DEPLOY_HOST", "DEPLOY_USER"):
			value, _ = _env_value(env, key)
			if value and _is_placeholder(value):
				_redacted_issue(
					issues,
					severity="error",
					category="deployment",
					key=key,
					message=f"{key} is still a placeholder.",
					remediation=f"Configure {key} only through the protected GitHub Environment.",
				)


def _check_mounted_credential_safety(issues: list[dict[str, Any]], env: Mapping[str, Any] | None) -> None:
	path_value, key = _env_value(
		env,
		"NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
		"FIREBASE_SERVICE_ACCOUNT_PATH",
		"NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH",
	)
	if not path_value:
		return
	path = Path(path_value)
	lowered = path.name.lower()
	if "example" in lowered or "sample" in lowered or lowered.endswith(".dist.json"):
		_redacted_issue(
			issues,
			severity="error",
			category="notifications",
			key=key or "FIREBASE_SERVICE_ACCOUNT_PATH",
			message="An example Firebase credential file appears to be mounted in production.",
			remediation="Mount the real credential from a secret source using a non-example filename.",
		)
		return
	try:
		if path.exists():
			mode = path.stat().st_mode & 0o777
			if mode & 0o077:
				_redacted_issue(
					issues,
					severity="error",
					category="notifications",
					key=key or "FIREBASE_SERVICE_ACCOUNT_PATH",
					message="Firebase credential file permissions are too broad.",
					remediation="Set ownership to the service account and permissions to 0600 or stricter.",
				)
			sample = path.read_text(encoding="utf-8", errors="ignore")[:8192].lower()
			if any(
				token in sample
				for token in ("replace-with", "example-project", "dummy-private-key", "your-project")
			):
				_redacted_issue(
					issues,
					severity="error",
					category="notifications",
					key=key or "FIREBASE_SERVICE_ACCOUNT_PATH",
					message="Mounted Firebase credential content appears to be an example or placeholder.",
					remediation="Rotate and mount a real production credential from the secret manager.",
				)
	except OSError:
		_redacted_issue(
			issues,
			severity="error",
			category="notifications",
			key=key or "FIREBASE_SERVICE_ACCOUNT_PATH",
			message="Firebase credential file metadata could not be safely inspected.",
			remediation="Verify the file exists, is readable by the worker, and is owned with restrictive permissions.",
		)


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
	seen: set[tuple[str, str, str, str]] = set()
	result: list[dict[str, Any]] = []
	for issue in issues:
		key = (
			str(issue.get("severity") or ""),
			str(issue.get("category") or ""),
			str(issue.get("key") or ""),
			str(issue.get("message") or ""),
		)
		if key in seen:
			continue
		seen.add(key)
		result.append(issue)
	return result


def validate_production_config(
	*,
	env: Mapping[str, Any] | None = None,
	site_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Return a redacted production-readiness config report.

	The returned report never includes secret values. It includes only variable
	names, issue categories, and remediation text.
	"""

	issues: list[dict[str, Any]] = []

	_check_environment_and_secret_sources(issues, env)
	_check_public_domains(issues, env)
	_check_storage(issues, env)
	_check_livekit(issues, env)
	_check_worker_services(issues, env)
	_check_ai_services(issues, env)
	_check_firebase(issues, env)
	_check_mounted_credential_safety(issues, env)
	_check_backup_encryption_and_alerting(issues, env)
	_check_maps(issues, env, site_config)

	issues = _dedupe_issues(issues)
	errors = [issue for issue in issues if issue.get("severity") == "error"]
	warnings = [issue for issue in issues if issue.get("severity") == "warning"]

	return {
		"ready": not errors,
		"summary": {
			"errors": len(errors),
			"warnings": len(warnings),
			"checks": 11,
		},
		"errors": errors,
		"warnings": warnings,
	}


def validate_staging_config(
	*,
	env: Mapping[str, Any] | None = None,
	site_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Validate staging with the same safety controls as production.

	Staging is expected to be production-like. The only relaxed invariant is
	the literal environment name; all secret, encryption, alerting, service,
	storage, callback, and credential checks remain fail-closed.
	"""

	values = dict(os.environ if env is None else env)
	actual_environment = _clean(values.get("AOS_ENVIRONMENT") or values.get("ENVIRONMENT")).lower()
	values["AOS_ENVIRONMENT"] = "production"
	report = validate_production_config(env=values, site_config=site_config)
	if actual_environment != "staging":
		issue = {
			"severity": "error",
			"category": "environment",
			"key": "AOS_ENVIRONMENT",
			"message": "Staging deployment validation requires AOS_ENVIRONMENT=staging.",
			"remediation": "Set AOS_ENVIRONMENT=staging on the staging host.",
		}
		report["errors"] = [issue, *report.get("errors", [])]
		report["ready"] = False
		report["summary"] = {
			**report.get("summary", {}),
			"errors": int(report.get("summary", {}).get("errors", 0)) + 1,
		}
	return report


def assert_staging_config_ready() -> dict[str, Any]:
	"""Bench-executable staging guard with production-equivalent safety."""

	report = validate_staging_config()
	if not report.get("ready"):
		count = report.get("summary", {}).get("errors", 0)
		raise ProductionConfigError(
			f"AOS staging configuration is not ready: {count} error(s). "
			"Run validate_staging_config for the redacted report."
		)
	return report


def validate_restore_rehearsal_config(
	*,
	env: Mapping[str, Any] | None = None,
	site_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
	"""Validate a non-production restore environment with production controls."""

	values = dict(os.environ if env is None else env)
	actual_environment = _clean(values.get("AOS_ENVIRONMENT") or values.get("ENVIRONMENT")).lower()
	values["AOS_ENVIRONMENT"] = "production"
	report = validate_production_config(env=values, site_config=site_config)
	if actual_environment not in {"staging", "rehearsal", "test"}:
		issue = {
			"severity": "error",
			"category": "environment",
			"key": "AOS_ENVIRONMENT",
			"message": "Restore rehearsal configuration must use staging, rehearsal, or test environment mode.",
			"remediation": "Set AOS_ENVIRONMENT to staging, rehearsal, or test; never production.",
		}
		report["errors"] = [issue, *report.get("errors", [])]
		report["ready"] = False
		report["summary"] = {
			**report.get("summary", {}),
			"errors": int(report.get("summary", {}).get("errors", 0)) + 1,
		}
	return report


def assert_restore_rehearsal_config_ready() -> dict[str, Any]:
	"""Bench-executable production-equivalent guard for restore rehearsals."""

	report = validate_restore_rehearsal_config()
	if not report.get("ready"):
		count = report.get("summary", {}).get("errors", 0)
		raise ProductionConfigError(
			f"AOS restore rehearsal configuration is not ready: {count} error(s). "
			"Run validate_restore_rehearsal_config for the redacted report."
		)
	return report


def assert_production_config_ready() -> dict[str, Any]:
	"""Bench-executable guard that raises when production config is not ready."""

	report = validate_production_config()
	if not report.get("ready"):
		count = report.get("summary", {}).get("errors", 0)
		raise ProductionConfigError(
			f"AOS production configuration is not ready: {count} error(s). "
			"Run validate_production_config for the redacted report."
		)
	return report


def production_config_summary() -> dict[str, Any]:
	"""Bench-friendly lightweight summary with no secrets."""

	report = validate_production_config()
	return {
		"ready": report.get("ready"),
		"summary": report.get("summary"),
		"errors": report.get("errors"),
		"warnings": report.get("warnings"),
	}


def report_contains_secret_value(report: Mapping[str, Any], secret_value: str) -> bool:
	"""Test helper: ensure a redacted report does not leak known secret values."""

	needle = _clean(secret_value)
	if not needle:
		return False
	return bool(re.search(re.escape(needle), repr(report)))
