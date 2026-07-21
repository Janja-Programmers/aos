"""Low-cardinality operational metrics and sanitized FastAPI error handling."""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import Counter
from typing import Any

from app.validation_fields import validation_fields
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import ValidationError

LOGGER = logging.getLogger("aos.operational")
_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_LOCK = threading.Lock()
_REQUESTS: Counter[tuple[str, str]] = Counter()
_EXCEPTIONS: Counter[str] = Counter()
_DURATION_COUNT: Counter[str] = Counter()
_DURATION_SUM: Counter[str] = Counter()
_DURATION_BUCKETS: Counter[tuple[str, float]] = Counter()
_DEPENDENCY_READY: dict[str, int] = {}
_SERVICE = "unknown"


def _surface(path: str) -> str:
	path = str(path or "")
	if path in {"/health", "/ready", "/metrics"}:
		return path.lstrip("/") or "root"
	if path.startswith("/jobs") or path.startswith("/events"):
		return "jobs"
	if path.startswith("/ads") or path.startswith("/shorts") or path.startswith("/search"):
		return "query"
	return "other"


def _category(exc: Exception) -> str:
	name = exc.__class__.__name__.lower()
	if any(token in name for token in ("timeout", "connection", "redis", "database")):
		return "dependency_unavailable"
	if any(token in name for token in ("validation", "value", "type")):
		return "invalid_request"
	return "internal_error"


async def request_validation_error_handler(request: Request, exc: RequestValidationError | ValidationError):
	fields = validation_fields(exc.errors())
	LOGGER.warning(
		"Sanitized request validation failure: method=%s surface=%s fields=%s",
		request.method,
		_surface(request.url.path),
		[field["field"] for field in fields],
	)
	return JSONResponse(
		status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
		content={
			"ok": False,
			"message": "Request validation failed.",
			"error": "VALIDATION_ERROR",
			"data": {"fields": fields},
		},
	)


def public_error(category: str, *, ready: bool | None = None) -> dict[str, Any]:
	body: dict[str, Any] = {
		"ok": False,
		"error": category.upper(),
		"category": category,
	}
	if ready is not None:
		body["ready"] = ready
	return body


def readiness_error(dependency: str, exc: Exception) -> JSONResponse:
	category = _category(exc)
	LOGGER.exception("Readiness dependency failed: %s", dependency)
	with _LOCK:
		_DEPENDENCY_READY[str(dependency)] = 0
		_EXCEPTIONS[category] += 1
	body = public_error(category, ready=False)
	body["dependency"] = str(dependency)
	return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)


def dependency_ready(dependency: str) -> None:
	with _LOCK:
		_DEPENDENCY_READY[str(dependency)] = 1


def _authorized_metrics(request: Request) -> bool:
	configured = os.getenv("AOS_METRICS_TOKEN", "").strip()
	authorization = request.headers.get("authorization", "")
	supplied = authorization.removeprefix("Bearer ").strip() if authorization.startswith("Bearer ") else ""
	if configured and supplied and hmac.compare_digest(supplied, configured):
		return True
	allow_loopback = os.getenv("AOS_METRICS_ALLOW_LOOPBACK", "true").strip().lower() in {
		"1",
		"true",
		"yes",
		"on",
	}
	client = request.client.host if request.client else ""
	return allow_loopback and client in {"127.0.0.1", "::1", "localhost", "testclient"}


def _escape(value: str) -> str:
	return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics() -> str:
	lines = [
		"# HELP aos_companion_http_requests_total HTTP requests by low-cardinality surface and status class.",
		"# TYPE aos_companion_http_requests_total counter",
	]
	with _LOCK:
		requests = dict(_REQUESTS)
		exceptions = dict(_EXCEPTIONS)
		duration_count = dict(_DURATION_COUNT)
		duration_sum = dict(_DURATION_SUM)
		duration_buckets = dict(_DURATION_BUCKETS)
		dependencies = dict(_DEPENDENCY_READY)
	for (surface, status_class), value in sorted(requests.items()):
		lines.append(
			f'aos_companion_http_requests_total{{service="{_escape(_SERVICE)}",surface="{_escape(surface)}",status_class="{status_class}"}} {value}'
		)
	lines.extend(
		[
			"# HELP aos_companion_http_request_duration_seconds Request duration histogram.",
			"# TYPE aos_companion_http_request_duration_seconds histogram",
		]
	)
	for surface in sorted(duration_count):
		cumulative = 0
		for bucket in _BUCKETS:
			cumulative = duration_buckets.get((surface, bucket), 0)
			lines.append(
				f'aos_companion_http_request_duration_seconds_bucket{{service="{_escape(_SERVICE)}",surface="{_escape(surface)}",le="{bucket:g}"}} {cumulative}'
			)
		lines.append(
			f'aos_companion_http_request_duration_seconds_bucket{{service="{_escape(_SERVICE)}",surface="{_escape(surface)}",le="+Inf"}} {duration_count[surface]}'
		)
		lines.append(
			f'aos_companion_http_request_duration_seconds_sum{{service="{_escape(_SERVICE)}",surface="{_escape(surface)}"}} {duration_sum[surface]:.9f}'
		)
		lines.append(
			f'aos_companion_http_request_duration_seconds_count{{service="{_escape(_SERVICE)}",surface="{_escape(surface)}"}} {duration_count[surface]}'
		)
	lines.extend(
		[
			"# HELP aos_companion_unhandled_exceptions_total Unhandled exceptions by sanitized category.",
			"# TYPE aos_companion_unhandled_exceptions_total counter",
		]
	)
	for category, value in sorted(exceptions.items()):
		lines.append(
			f'aos_companion_unhandled_exceptions_total{{service="{_escape(_SERVICE)}",category="{_escape(category)}"}} {value}'
		)
	lines.extend(
		[
			"# HELP aos_companion_dependency_ready Dependency readiness (1 ready, 0 not ready).",
			"# TYPE aos_companion_dependency_ready gauge",
		]
	)
	for dependency, value in sorted(dependencies.items()):
		lines.append(
			f'aos_companion_dependency_ready{{service="{_escape(_SERVICE)}",dependency="{_escape(dependency)}"}} {value}'
		)
	return "\n".join(lines) + "\n# EOF\n"


def instrument_app(app: FastAPI, service_name: str) -> None:
	global _SERVICE
	_SERVICE = str(service_name or "unknown")
	app.add_exception_handler(RequestValidationError, request_validation_error_handler)
	app.add_exception_handler(ValidationError, request_validation_error_handler)

	@app.middleware("http")
	async def operational_metrics_middleware(request: Request, call_next):
		started = time.perf_counter()
		surface = _surface(request.url.path)
		try:
			response = await call_next(request)
		except Exception as exc:
			category = _category(exc)
			LOGGER.exception("Unhandled companion-service exception")
			with _LOCK:
				_EXCEPTIONS[category] += 1
			response = JSONResponse(status_code=500, content=public_error(category))
		elapsed = max(0.0, time.perf_counter() - started)
		status_class = f"{int(response.status_code) // 100}xx"
		with _LOCK:
			_REQUESTS[(surface, status_class)] += 1
			_DURATION_COUNT[surface] += 1
			_DURATION_SUM[surface] += elapsed
			for bucket in _BUCKETS:
				if elapsed <= bucket:
					_DURATION_BUCKETS[(surface, bucket)] += 1
		return response

	@app.get("/metrics", include_in_schema=False)
	async def operational_metrics(request: Request):
		if not _authorized_metrics(request):
			return JSONResponse(status_code=403, content=public_error("metrics_access_denied"))
		return PlainTextResponse(
			render_metrics(),
			media_type="application/openmetrics-text; version=1.0.0; charset=utf-8",
		)
