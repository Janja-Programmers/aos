"""Read-only operational health checks for AOS infrastructure.

Traffic readiness is intentionally shallow and cheap. Deep operational health
adds configuration, storage and feature-service probes for operators, but is
never used as a per-request or load-balancer hot path.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import frappe
import requests

from aos.services.storage.s3_compatible import S3CompatibleStorage
from aos.utils import aos_config
from aos.utils.health_model import (
	AVAILABLE,
	DEGRADED,
	DISABLED,
	DISABLED_CONDITION,
	HEALTHY,
	MISCONFIGURED,
	OPTIONAL,
	REQUIRED,
	TIMED_OUT,
	UNAVAILABLE,
	UNHEALTHY,
	UNKNOWN,
	health_check,
	summarize_health,
)
from aos.utils.production_config import validate_production_config

DEFAULT_EXTERNAL_TIMEOUT_SECONDS = 3
MAX_EXTERNAL_TIMEOUT_SECONDS = 10
MAX_HTTP_PROBE_WORKERS = 8


@dataclass(frozen=True)
class ServiceEndpoint:
	name: str
	category: str
	url: str
	health_path: str = "/health"
	ready_path: str | None = "/ready"
	enabled: bool = True
	requirement: str = OPTIONAL


def _clean(value: Any) -> str:
	return str(value or "").strip()


def _bool_env(env: Mapping[str, Any] | None, key: str, default: bool) -> bool:
	value = _clean((env or {}).get(key) if env is not None else aos_config.get_env(key))
	if not value:
		return bool(default)
	return value.lower() in {"1", "true", "yes", "y", "on"}


def _env(env: Mapping[str, Any] | None, key: str, default: str = "") -> str:
	if env is not None:
		return _clean(env.get(key) or default)
	return _clean(aos_config.get_env(key, default) or default)


def _site_value(site_config: Mapping[str, Any] | None, key: str) -> str:
	if site_config is not None:
		return _clean(site_config.get(key))
	try:
		return _clean(frappe.conf.get(key))
	except Exception:
		return ""


def _redacted_url(url: str) -> str:
	"""Return a credential/query-free origin/path or an empty string."""

	value = _clean(url)
	if not value:
		return ""
	try:
		parsed = urlparse(value if "://" in value else f"http://{value}")
		host = parsed.hostname or ""
		if not host:
			return ""
		port = f":{parsed.port}" if parsed.port else ""
		return f"{parsed.scheme}://{host}{port}"
	except (TypeError, ValueError):
		return ""


def _join_url(base_url: str, path: str) -> str:
	base = _clean(base_url).rstrip("/") + "/"
	return urljoin(base, str(path or "").lstrip("/"))


def _safe_payload(payload: Any) -> dict[str, Any]:
	"""Keep only reviewed, non-sensitive scalar health metadata."""

	if not isinstance(payload, dict):
		return {}
	allowed = {
		"ok",
		"ready",
		"service",
		"environment",
		"mode",
		"model_loaded",
		"processor_loaded",
		"vector_store_ready",
		"device",
		"compute_type",
		"model_name",
		"model_version",
	}
	return {
		key: value
		for key in allowed
		if key in payload
		and ((value := payload.get(key)) is None or isinstance(value, (str, int, float, bool)))
	}


def _database_check(*, database_probe: Callable[[], Any] | None = None) -> dict[str, Any]:
	try:
		if database_probe is not None:
			database_probe()
		else:
			rows = frappe.db.sql("SELECT 1")
			if not rows or int(rows[0][0]) != 1:
				raise RuntimeError("database probe returned an unexpected result")
		return health_check(
			name="database",
			category="database",
			status=HEALTHY,
			requirement=REQUIRED,
			condition=AVAILABLE,
			message="MariaDB is reachable.",
		)
	except Exception:
		return health_check(
			name="database",
			category="database",
			status=UNHEALTHY,
			requirement=REQUIRED,
			condition=UNAVAILABLE,
			message="MariaDB is not reachable.",
		)


def _redis_cache_check(*, cache_probe: Callable[[], Any] | None = None) -> dict[str, Any]:
	try:
		if cache_probe is not None:
			cache_probe()
		else:
			cache = frappe.cache()
			ping = getattr(cache, "ping", None)
			if callable(ping):
				ping()
			else:
				client = getattr(cache, "redis_server", None) or getattr(cache, "_redis", None)
				if client is None or not hasattr(client, "ping"):
					raise RuntimeError("cache Redis client is unavailable")
				client.ping()
		return health_check(
			name="redis_cache",
			category="redis",
			status=HEALTHY,
			requirement=REQUIRED,
			condition=AVAILABLE,
			message="Frappe cache Redis is reachable.",
		)
	except Exception:
		return health_check(
			name="redis_cache",
			category="redis",
			status=UNHEALTHY,
			requirement=REQUIRED,
			condition=UNAVAILABLE,
			message="Frappe cache Redis is not reachable.",
		)


def _redis_queue_check(*, queue_probe: Callable[[], Any] | None = None) -> dict[str, Any]:
	try:
		if queue_probe is not None:
			queue_probe()
		else:
			from frappe.utils.background_jobs import get_redis_conn

			get_redis_conn().ping()
		return health_check(
			name="redis_queue",
			category="redis",
			status=HEALTHY,
			requirement=REQUIRED,
			condition=AVAILABLE,
			message="Frappe queue Redis is reachable.",
		)
	except Exception:
		return health_check(
			name="redis_queue",
			category="redis",
			status=UNHEALTHY,
			requirement=REQUIRED,
			condition=UNAVAILABLE,
			message="Frappe queue Redis is not reachable.",
		)


def validate_liveness() -> dict[str, Any]:
	"""Return process liveness without probing any dependency or shared state."""

	return {
		"status": HEALTHY,
		"alive": True,
	}


def validate_readiness(
	*,
	database_probe: Callable[[], Any] | None = None,
	cache_probe: Callable[[], Any] | None = None,
	queue_probe: Callable[[], Any] | None = None,
) -> dict[str, Any]:
	"""Return cheap traffic readiness for the current Frappe web node."""

	checks = [
		_database_check(database_probe=database_probe),
		_redis_cache_check(cache_probe=cache_probe),
		_redis_queue_check(queue_probe=queue_probe),
	]
	return summarize_health(checks)


def _status_for_http_response(response: Any, *, expect_ready: bool) -> tuple[str, str, str]:
	status_code = int(getattr(response, "status_code", 0) or 0)
	try:
		payload = response.json()
	except Exception:
		payload = None

	if status_code == 0 or status_code >= 500:
		return UNHEALTHY, UNAVAILABLE, "Service returned an unhealthy response."
	if status_code >= 400:
		return UNHEALTHY, MISCONFIGURED, "Service returned a non-success response."
	if expect_ready:
		if not isinstance(payload, dict):
			return UNKNOWN, "unknown", "Service readiness response is malformed."
		ready_value = payload.get("ready", payload.get("ok"))
		if ready_value is True:
			return HEALTHY, AVAILABLE, "Service reports ready."
		if ready_value is False:
			return UNHEALTHY, UNAVAILABLE, "Service reports not ready."
		return UNKNOWN, "unknown", "Service readiness response is missing a readiness signal."
	if isinstance(payload, dict) and payload.get("ok") is False:
		return UNHEALTHY, UNAVAILABLE, "Service reports not ok."
	return HEALTHY, AVAILABLE, "Service is reachable."


def _http_check(
	*,
	endpoint: ServiceEndpoint,
	path: str,
	timeout_seconds: int,
	http_get: Callable[..., Any],
	expect_ready: bool,
) -> dict[str, Any]:
	url = _join_url(endpoint.url, path)
	redacted = _redacted_url(url)
	try:
		response = http_get(url, timeout=timeout_seconds)
		status, condition, message = _status_for_http_response(response, expect_ready=expect_ready)
		try:
			payload = response.json()
		except Exception:
			payload = None
		details = {
			"url": redacted,
			"http_status": int(getattr(response, "status_code", 0) or 0),
			"response": _safe_payload(payload),
		}
	except requests.Timeout:
		status, condition, message = UNKNOWN, TIMED_OUT, "Service probe timed out."
		details = {"url": redacted, "timeout_seconds": timeout_seconds}
	except Exception:
		status, condition, message = UNHEALTHY, UNAVAILABLE, "Service is not reachable."
		details = {"url": redacted}

	return health_check(
		name=f"{endpoint.name}_{path.strip('/') or 'root'}",
		category=endpoint.category,
		status=status,
		requirement=endpoint.requirement,
		condition=condition,
		message=message,
		details=details,
	)


def _disabled_endpoint_check(endpoint: ServiceEndpoint) -> dict[str, Any]:
	return health_check(
		name=endpoint.name,
		category=endpoint.category,
		status=DISABLED,
		requirement=endpoint.requirement,
		condition=DISABLED_CONDITION,
		message="Service is disabled by configuration.",
		details={"url": _redacted_url(endpoint.url)},
	)


def _misconfigured_endpoint_check(endpoint: ServiceEndpoint) -> dict[str, Any]:
	return health_check(
		name=endpoint.name,
		category=endpoint.category,
		status=UNHEALTHY,
		requirement=endpoint.requirement,
		condition=MISCONFIGURED,
		message="Service URL is not configured.",
	)


def _external_service_endpoints(env: Mapping[str, Any] | None) -> list[ServiceEndpoint]:
	return [
		ServiceEndpoint("video_processing", "external_service", _env(env, "VIDEO_SERVICE_URL", "")),
		ServiceEndpoint(
			"moderation",
			"external_service",
			_env(env, "MODERATION_SERVICE_URL", ""),
			enabled=_bool_env(env, "MODERATION_ENABLED", True),
		),
		ServiceEndpoint(
			"search_ranking",
			"external_service",
			_env(env, "SEARCH_RANKING_SERVICE_URL", ""),
			enabled=_bool_env(env, "SEARCH_RANKING_ENABLED", True),
		),
		ServiceEndpoint(
			"analytics_pipeline",
			"external_service",
			_env(env, "ANALYTICS_SERVICE_URL", ""),
			enabled=_bool_env(env, "ANALYTICS_PIPELINE_ENABLED", True),
		),
		ServiceEndpoint(
			"notification_delivery",
			"external_service",
			_env(env, "NOTIFICATION_SERVICE_URL", ""),
			enabled=_bool_env(env, "NOTIFICATION_DELIVERY_ENABLED", True),
		),
		ServiceEndpoint("translation", "ai_ml", _env(env, "TRANSLATION_SERVICE_URL", "")),
		ServiceEndpoint("image_search", "ai_ml", _env(env, "IMAGE_SEARCH_SERVICE_URL", "")),
		ServiceEndpoint(
			"background_removal",
			"ai_ml",
			_env(env, "BACKGROUND_REMOVAL_SERVICE_URL", ""),
		),
		ServiceEndpoint("text_safety", "ai_ml", _env(env, "TEXT_SAFETY_SERVICE_URL", "")),
	]


def _map_endpoints(
	env: Mapping[str, Any] | None, site_config: Mapping[str, Any] | None
) -> list[ServiceEndpoint]:
	return [
		ServiceEndpoint(
			"basemap_origin",
			"maps",
			_env(env, "MAPS_PUBLIC_BASE_URL", ""),
			health_path="/current.json",
			ready_path=None,
		),
		ServiceEndpoint(
			"photon",
			"maps",
			_site_value(site_config, "photon_base_url"),
			health_path="/status",
			ready_path=None,
			enabled=_site_value(site_config, "maps_photon_enabled").lower() in {"1", "true", "yes", "on"},
		),
		ServiceEndpoint(
			"nominatim_fallback",
			"maps",
			_site_value(site_config, "nominatim_base_url"),
			health_path="/status.php",
			ready_path=None,
			enabled=_site_value(site_config, "maps_nominatim_fallback_enabled").lower()
			in {"1", "true", "yes", "on"},
		),
		ServiceEndpoint(
			"valhalla",
			"maps",
			_site_value(site_config, "valhalla_base_url"),
			health_path="/status",
			ready_path=None,
			enabled=_site_value(site_config, "maps_routing_enabled").lower() in {"1", "true", "yes", "on"},
		),
	]


def _livekit_endpoint(env: Mapping[str, Any] | None) -> ServiceEndpoint:
	return ServiceEndpoint(
		name="livekit",
		category="livekit",
		url=_env(env, "LIVEKIT_ADMIN_ENDPOINT", ""),
		health_path="/",
		ready_path=None,
	)


def _storage_check(
	*,
	storage_factory: Callable[[], Any],
	timeout_seconds: int,
) -> dict[str, Any]:
	try:
		storage = storage_factory()
		try:
			report = storage.healthcheck(timeout_seconds=timeout_seconds)
		except TypeError:
			report = storage.healthcheck()
		if not isinstance(report, Mapping):
			return health_check(
				name="object_storage",
				category="storage",
				status=UNKNOWN,
				requirement=REQUIRED,
				condition="unknown",
				message="Object storage returned a malformed health response.",
			)
		if not bool(report.get("ok")):
			condition = MISCONFIGURED if _clean(report.get("category")) == "configuration" else UNAVAILABLE
			return health_check(
				name="object_storage",
				category="storage",
				status=UNHEALTHY,
				requirement=REQUIRED,
				condition=condition,
				message="Object storage or configured media buckets are not ready.",
				details={
					"latency_ms": int(report.get("latency_ms") or 0),
					"configured_bucket_count": int(report.get("configured_bucket_count") or 0),
					"missing_bucket_count": int(report.get("missing_bucket_count") or 0),
				},
			)
		return health_check(
			name="object_storage",
			category="storage",
			status=HEALTHY,
			requirement=REQUIRED,
			condition=AVAILABLE,
			message="Object storage and configured media buckets are ready.",
			details={
				"latency_ms": int(report.get("latency_ms") or 0),
				"configured_bucket_count": int(report.get("configured_bucket_count") or 0),
				"missing_bucket_count": int(report.get("missing_bucket_count") or 0),
			},
		)
	except Exception:
		return health_check(
			name="object_storage",
			category="storage",
			status=UNHEALTHY,
			requirement=REQUIRED,
			condition=UNAVAILABLE,
			message="Object storage health probe failed.",
		)


def _firebase_credentials_check(*, env: Mapping[str, Any] | None) -> dict[str, Any]:
	if not _bool_env(env, "NOTIFICATION_DELIVERY_ENABLED", True):
		return health_check(
			name="firebase_credentials",
			category="notifications",
			status=DISABLED,
			requirement=OPTIONAL,
			condition=DISABLED_CONDITION,
			message="Notification delivery is disabled by configuration.",
		)
	path = _env(env, "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH", "") or _env(
		env, "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH", ""
	)
	if not path:
		return health_check(
			name="firebase_credentials",
			category="notifications",
			status=UNHEALTHY,
			requirement=OPTIONAL,
			condition=MISCONFIGURED,
			message="Firebase service-account path is not configured.",
		)
	try:
		exists = os.path.isfile(path)
	except Exception:
		exists = False
	return health_check(
		name="firebase_credentials",
		category="notifications",
		status=HEALTHY if exists else UNHEALTHY,
		requirement=OPTIONAL,
		condition=AVAILABLE if exists else MISCONFIGURED,
		message="Firebase service-account file is available."
		if exists
		else "Firebase service-account file is not available to the backend host.",
		details={"path_configured": True},
	)


def _production_config_check(
	*, env: Mapping[str, Any] | None, site_config: Mapping[str, Any] | None
) -> dict[str, Any]:
	try:
		report = validate_production_config(env=env, site_config=site_config)
		summary = dict(report.get("summary") or {})
		ready = bool(report.get("ready"))
		return health_check(
			name="production_config",
			category="configuration",
			status=HEALTHY if ready else UNHEALTHY,
			requirement=REQUIRED,
			condition=AVAILABLE if ready else MISCONFIGURED,
			message="Production configuration gate is ready."
			if ready
			else "Production configuration gate is not ready.",
			details={
				"errors": int(summary.get("errors") or 0),
				"warnings": int(summary.get("warnings") or 0),
				"checks": int(summary.get("checks") or 0),
			},
		)
	except Exception:
		return health_check(
			name="production_config",
			category="configuration",
			status=UNKNOWN,
			requirement=REQUIRED,
			condition="unknown",
			message="Production configuration validation could not be completed.",
		)


def _http_probe_plan(
	*,
	env: Mapping[str, Any] | None,
	site_config: Mapping[str, Any] | None,
	include_ready: bool,
) -> tuple[list[dict[str, Any]], list[tuple[ServiceEndpoint, str, bool]]]:
	immediate: list[dict[str, Any]] = []
	planned: list[tuple[ServiceEndpoint, str, bool]] = []
	endpoints = [_livekit_endpoint(env), *_external_service_endpoints(env), *_map_endpoints(env, site_config)]
	for endpoint in endpoints:
		if not endpoint.enabled:
			immediate.append(_disabled_endpoint_check(endpoint))
			continue
		if not _clean(endpoint.url):
			immediate.append(_misconfigured_endpoint_check(endpoint))
			continue
		planned.append((endpoint, endpoint.health_path, False))
		if include_ready and endpoint.ready_path:
			planned.append((endpoint, endpoint.ready_path, True))
	return immediate, planned


def _run_http_probes(
	planned: list[tuple[ServiceEndpoint, str, bool]],
	*,
	timeout_seconds: int,
	http_get: Callable[..., Any],
) -> list[dict[str, Any]]:
	if not planned:
		return []
	workers = max(1, min(MAX_HTTP_PROBE_WORKERS, len(planned)))
	with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="aos-diagnostics") as executor:
		futures = [
			executor.submit(
				_http_check,
				endpoint=endpoint,
				path=path,
				timeout_seconds=timeout_seconds,
				http_get=http_get,
				expect_ready=expect_ready,
			)
			for endpoint, path, expect_ready in planned
		]
		# Preserve service/path ordering so repeated reports are deterministic.
		return [future.result() for future in futures]


def validate_operational_health(
	*,
	env: Mapping[str, Any] | None = None,
	site_config: Mapping[str, Any] | None = None,
	timeout_seconds: int = DEFAULT_EXTERNAL_TIMEOUT_SECONDS,
	include_ready: bool = True,
	http_get: Callable[..., Any] | None = None,
	storage_factory: Callable[[], Any] | None = None,
	database_probe: Callable[[], Any] | None = None,
	cache_probe: Callable[[], Any] | None = None,
	queue_probe: Callable[[], Any] | None = None,
) -> dict[str, Any]:
	"""Return deterministic, redacted deep operational health for operators.

	External HTTP probes run concurrently with strict per-probe timeouts. This is
	still a deep operator diagnostic, not a load-balancer readiness endpoint.
	"""

	timeout = max(1, min(int(timeout_seconds or DEFAULT_EXTERNAL_TIMEOUT_SECONDS), MAX_EXTERNAL_TIMEOUT_SECONDS))
	http_get = http_get or requests.get
	storage_factory = storage_factory or S3CompatibleStorage

	traffic_readiness = validate_readiness(
		database_probe=database_probe,
		cache_probe=cache_probe,
		queue_probe=queue_probe,
	)
	checks = list(traffic_readiness.get("checks") or [])
	checks.append(_production_config_check(env=env, site_config=site_config))
	checks.append(_storage_check(storage_factory=storage_factory, timeout_seconds=timeout))
	checks.append(_firebase_credentials_check(env=env))

	immediate, planned = _http_probe_plan(env=env, site_config=site_config, include_ready=include_ready)
	checks.extend(immediate)
	checks.extend(_run_http_probes(planned, timeout_seconds=timeout, http_get=http_get))

	report = summarize_health(checks)
	report["traffic_ready"] = bool(traffic_readiness.get("ready"))
	report["probe_timeout_seconds"] = timeout
	return report


def liveness_summary() -> dict[str, Any]:
	return validate_liveness()


def readiness_summary() -> dict[str, Any]:
	return validate_readiness()


def operational_health_summary() -> dict[str, Any]:
	return validate_operational_health()


def assert_operational_health_ready() -> dict[str, Any]:
	"""Deployment gate: raise when a required operational dependency is unhealthy."""

	report = validate_operational_health()
	if not report.get("ready"):
		blocking = int(((report.get("summary") or {}).get("required") or {}).get("blocking") or 0)
		raise RuntimeError(f"AOS operational health is not ready: {blocking} required check(s) blocking.")
	return report
