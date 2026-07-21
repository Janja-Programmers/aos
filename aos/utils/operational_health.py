"""Admin-facing operational health checks for AOS services.

The checks in this module are intentionally read-only. They verify that the
backend can reach the core infrastructure and companion services used by AOS,
while returning only safe/redacted metadata to callers.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import frappe
import requests

from aos.services.storage.minio_storage import MinioStorage
from aos.utils import aos_config
from aos.utils.production_config import validate_production_config

HealthStatus = str


@dataclass(frozen=True)
class ServiceEndpoint:
	name: str
	category: str
	url: str
	health_path: str = "/health"
	ready_path: str | None = "/ready"
	enabled: bool = True
	ready_required: bool = True


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
	"""Return a redacted URL origin/path without credentials/query/fragment."""

	value = _clean(url)
	if not value:
		return ""
	parsed = urlparse(value if "://" in value else f"http://{value}")
	host = parsed.hostname or ""
	if not host:
		return ""
	port = f":{parsed.port}" if parsed.port else ""
	path = parsed.path.rstrip("/")
	return f"{parsed.scheme}://{host}{port}{path}"


def _join_url(base_url: str, path: str) -> str:
	base = _clean(base_url).rstrip("/") + "/"
	return urljoin(base, str(path or "").lstrip("/"))


def _safe_payload(payload: Any) -> dict[str, Any]:
	"""Keep only non-sensitive health metadata from service responses."""

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
	}
	safe: dict[str, Any] = {}
	for key in allowed:
		if key in payload:
			value = payload.get(key)
			if isinstance(value, (str, int, float, bool)) or value is None:
				safe[key] = value
	return safe


def _check(
	checks: list[dict[str, Any]],
	*,
	name: str,
	category: str,
	status: HealthStatus,
	message: str,
	details: Mapping[str, Any] | None = None,
) -> None:
	checks.append(
		{
			"name": name,
			"category": category,
			"status": status,
			"message": message,
			"details": dict(details or {}),
		}
	)


def _status_for_http_response(
	response: Any,
	*,
	expect_ready: bool = False,
	ready_required: bool = True,
) -> tuple[HealthStatus, str]:
	status_code = int(getattr(response, "status_code", 0) or 0)
	try:
		payload = response.json()
	except Exception:
		payload = {}

	if status_code >= 500 or status_code == 0:
		return "unhealthy", "Service returned an unhealthy response."
	if status_code >= 400:
		if expect_ready and not ready_required:
			return "degraded", "Optional readiness endpoint is not available or not ready."
		return "degraded", "Service is reachable but returned a client/error response."
	if isinstance(payload, dict):
		if payload.get("ok") is False:
			return "unhealthy", "Service reports not ok."
		if expect_ready and payload.get("ready") is False:
			if ready_required:
				return "unhealthy", "Service reports not ready."
			return "degraded", "Optional readiness check reports not ready."
	return "healthy", "Service is reachable."


def _http_check(
	*,
	endpoint: ServiceEndpoint,
	path: str,
	timeout_seconds: int,
	http_get: Callable[..., Any],
	expect_ready: bool = False,
) -> dict[str, Any]:
	url = _join_url(endpoint.url, path)
	redacted = _redacted_url(url)
	try:
		response = http_get(url, timeout=timeout_seconds)
		status, message = _status_for_http_response(
			response,
			expect_ready=expect_ready,
			ready_required=endpoint.ready_required,
		)
		try:
			payload = response.json()
		except Exception:
			payload = {}
		details = {
			"url": redacted,
			"http_status": int(getattr(response, "status_code", 0) or 0),
			"response": _safe_payload(payload),
		}
	except Exception:
		if expect_ready and not endpoint.ready_required:
			status = "degraded"
			message = "Optional readiness endpoint is not reachable."
		else:
			status = "unhealthy"
			message = "Service is not reachable."
		details = {"url": redacted}

	return {
		"name": f"{endpoint.name}_{path.strip('/') or 'root'}",
		"category": endpoint.category,
		"status": status,
		"message": message,
		"details": details,
	}


def _check_http_service(
	checks: list[dict[str, Any]],
	*,
	endpoint: ServiceEndpoint,
	timeout_seconds: int,
	http_get: Callable[..., Any],
	include_ready: bool,
) -> None:
	if not endpoint.enabled:
		_check(
			checks,
			name=endpoint.name,
			category=endpoint.category,
			status="skipped",
			message="Service is disabled by configuration.",
			details={"url": _redacted_url(endpoint.url)},
		)
		return

	if not _clean(endpoint.url):
		_check(
			checks,
			name=endpoint.name,
			category=endpoint.category,
			status="unhealthy",
			message="Service URL is not configured.",
		)
		return

	checks.append(
		_http_check(
			endpoint=endpoint,
			path=endpoint.health_path,
			timeout_seconds=timeout_seconds,
			http_get=http_get,
		)
	)
	if include_ready and endpoint.ready_path:
		checks.append(
			_http_check(
				endpoint=endpoint,
				path=endpoint.ready_path,
				timeout_seconds=timeout_seconds,
				http_get=http_get,
				expect_ready=True,
			)
		)


def _external_service_endpoints(env: Mapping[str, Any] | None) -> list[ServiceEndpoint]:
	return [
		ServiceEndpoint(
			name="video_processing",
			category="external_service",
			url=_env(env, "VIDEO_SERVICE_URL", "http://127.0.0.1:8130"),
		),
		ServiceEndpoint(
			name="moderation",
			category="external_service",
			url=_env(env, "MODERATION_SERVICE_URL", "http://127.0.0.1:8140"),
			enabled=_bool_env(env, "MODERATION_ENABLED", True),
		),
		ServiceEndpoint(
			name="search_ranking",
			category="external_service",
			url=_env(env, "SEARCH_RANKING_SERVICE_URL", "http://127.0.0.1:8150"),
			enabled=_bool_env(env, "SEARCH_RANKING_ENABLED", True),
		),
		ServiceEndpoint(
			name="analytics_pipeline",
			category="external_service",
			url=_env(env, "ANALYTICS_SERVICE_URL", "http://127.0.0.1:8170"),
			enabled=_bool_env(env, "ANALYTICS_PIPELINE_ENABLED", True),
		),
		ServiceEndpoint(
			name="notification_delivery",
			category="external_service",
			url=_env(env, "NOTIFICATION_SERVICE_URL", "http://127.0.0.1:8160"),
			enabled=_bool_env(env, "NOTIFICATION_DELIVERY_ENABLED", True),
		),
		ServiceEndpoint(
			name="translation",
			category="ai_ml",
			url=_env(env, "TRANSLATION_SERVICE_URL", "http://127.0.0.1:8100"),
		),
		ServiceEndpoint(
			name="image_search",
			category="ai_ml",
			url=_env(env, "IMAGE_SEARCH_SERVICE_URL", "http://127.0.0.1:8110"),
			ready_required=False,
		),
		ServiceEndpoint(
			name="background_removal",
			category="ai_ml",
			url=_env(env, "BACKGROUND_REMOVAL_SERVICE_URL", "http://127.0.0.1:8120"),
		),
	]


def _map_endpoints(
	env: Mapping[str, Any] | None, site_config: Mapping[str, Any] | None
) -> list[ServiceEndpoint]:
	return [
		ServiceEndpoint(
			name="tileserver",
			category="maps",
			url=_env(env, "TILESERVER_PUBLIC_URL", ""),
			health_path="/styles.json",
			ready_path=None,
		),
		ServiceEndpoint(
			name="photon",
			category="maps",
			url=_site_value(site_config, "photon_base_url"),
			health_path="/api?q=Nairobi&limit=1",
			ready_path=None,
		),
		ServiceEndpoint(
			name="nominatim",
			category="maps",
			url=_site_value(site_config, "nominatim_base_url"),
			health_path="/status.php",
			ready_path=None,
		),
		ServiceEndpoint(
			name="valhalla",
			category="maps",
			url=_site_value(site_config, "valhalla_base_url"),
			health_path="/status",
			ready_path=None,
		),
	]


def _livekit_http_url(env: Mapping[str, Any] | None) -> str:
	endpoint = _env(env, "LIVEKIT_ENDPOINT", "")
	if not endpoint:
		domain = _env(env, "AOS_LIVEKIT_DOMAIN", "")
		endpoint = f"wss://{domain}" if domain else ""
	if endpoint.startswith("wss://"):
		return "https://" + endpoint.removeprefix("wss://")
	if endpoint.startswith("ws://"):
		return "http://" + endpoint.removeprefix("ws://")
	return endpoint


def _check_storage(
	checks: list[dict[str, Any]],
	*,
	storage_factory: Callable[[], Any],
) -> None:
	try:
		storage = storage_factory()
		report = storage.healthcheck()
		if not bool(report.get("ok")):
			raise RuntimeError("Storage readiness failed")
		_check(
			checks,
			name="minio_storage",
			category="storage",
			status="healthy",
			message="Storage service, configured buckets, and public URL are ready.",
			details={
				"latency_ms": int(report.get("latency_ms") or 0),
				"configured_bucket_count": int(report.get("configured_bucket_count") or 0),
				"missing_bucket_count": int(report.get("missing_bucket_count") or 0),
			},
		)
	except Exception:
		_check(
			checks,
			name="minio_storage",
			category="storage",
			status="unhealthy",
			message="Storage service or configured media buckets are not ready.",
		)


def _check_frappe_redis(checks: list[dict[str, Any]]) -> None:
	try:
		cache = frappe.cache()
		ping = getattr(cache, "ping", None)
		if callable(ping):
			ping()
		else:
			client = getattr(cache, "redis_server", None) or getattr(cache, "_redis", None)
			if client is not None and hasattr(client, "ping"):
				client.ping()
		_check(
			checks,
			name="frappe_redis_cache",
			category="queue",
			status="healthy",
			message="Frappe Redis cache is reachable.",
		)
	except Exception:
		_check(
			checks,
			name="frappe_redis_cache",
			category="queue",
			status="unhealthy",
			message="Frappe Redis cache is not reachable.",
		)


def _check_firebase_credentials(checks: list[dict[str, Any]], *, env: Mapping[str, Any] | None) -> None:
	path = _env(env, "NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_HOST_PATH", "") or _env(
		env,
		"NOTIFICATION_FIREBASE_SERVICE_ACCOUNT_PATH",
		"",
	)
	if not path:
		_check(
			checks,
			name="firebase_credentials",
			category="notifications",
			status="unhealthy",
			message="Firebase service account path is not configured.",
		)
		return
	try:
		exists = os.path.exists(path)
	except Exception:
		exists = False
	_check(
		checks,
		name="firebase_credentials",
		category="notifications",
		status="healthy" if exists else "degraded",
		message=(
			"Firebase service account file exists."
			if exists
			else "Firebase service account file was not found from the backend host."
		),
		details={"path_configured": True},
	)


def _check_production_config(
	checks: list[dict[str, Any]],
	*,
	env: Mapping[str, Any] | None,
	site_config: Mapping[str, Any] | None,
) -> None:
	report = validate_production_config(env=env, site_config=site_config)
	summary = dict(report.get("summary") or {})
	_check(
		checks,
		name="production_config",
		category="configuration",
		status="healthy" if report.get("ready") else "unhealthy",
		message="Production configuration gate is ready."
		if report.get("ready")
		else "Production configuration gate is not ready.",
		details={
			"errors": int(summary.get("errors") or 0),
			"warnings": int(summary.get("warnings") or 0),
			"checks": int(summary.get("checks") or 0),
		},
	)


def validate_operational_health(
	*,
	env: Mapping[str, Any] | None = None,
	site_config: Mapping[str, Any] | None = None,
	timeout_seconds: int = 3,
	include_ready: bool = True,
	http_get: Callable[..., Any] | None = None,
	storage_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
	"""Return a redacted operational health report for AOS infrastructure."""

	checks: list[dict[str, Any]] = []
	timeout = max(1, min(int(timeout_seconds or 3), 15))
	http_get = http_get or requests.get
	storage_factory = storage_factory or MinioStorage

	_check_production_config(checks, env=env, site_config=site_config)
	_check_storage(checks, storage_factory=storage_factory)
	_check_frappe_redis(checks)
	_check_firebase_credentials(checks, env=env)

	livekit_url = _livekit_http_url(env)
	_check_http_service(
		checks,
		endpoint=ServiceEndpoint(
			name="livekit",
			category="livekit",
			url=livekit_url,
			health_path="/",
			ready_path=None,
		),
		timeout_seconds=timeout,
		http_get=http_get,
		include_ready=False,
	)

	for endpoint in _external_service_endpoints(env):
		_check_http_service(
			checks,
			endpoint=endpoint,
			timeout_seconds=timeout,
			http_get=http_get,
			include_ready=include_ready,
		)

	for endpoint in _map_endpoints(env, site_config):
		_check_http_service(
			checks,
			endpoint=endpoint,
			timeout_seconds=timeout,
			http_get=http_get,
			include_ready=False,
		)

	counts = {
		"healthy": 0,
		"degraded": 0,
		"unhealthy": 0,
		"skipped": 0,
		"checks": len(checks),
	}
	for check in checks:
		status = str(check.get("status") or "unhealthy")
		if status not in counts:
			status = "unhealthy"
		counts[status] += 1

	ready = counts["unhealthy"] == 0
	return {
		"ready": ready,
		"summary": counts,
		"checks": checks,
	}


def operational_health_summary() -> dict[str, Any]:
	"""Bench-friendly operational health report."""

	return validate_operational_health()


def assert_operational_health_ready() -> dict[str, Any]:
	"""Raise when any required operational-health check is unhealthy."""
	report = validate_operational_health()
	if not report.get("ready"):
		unhealthy = int((report.get("summary") or {}).get("unhealthy") or 0)
		raise RuntimeError(f"AOS operational health is not ready: {unhealthy} unhealthy check(s).")
	return report
