from __future__ import annotations

import json
from types import SimpleNamespace

from app import main
from app.security import build_signature, verify_signature
from fastapi.testclient import TestClient

SECRET = "unit-test-secret"


def settings(**overrides):
	values = {
		"service_name": "aos-analytics-pipeline",
		"environment": "test",
		"request_secret": SECRET,
		"queue_name": "analytics-pipeline",
		"job_timeout_seconds": 600,
		"result_ttl_seconds": 60,
		"failure_ttl_seconds": 60,
		"max_events_per_job": 2,
	}
	values.update(overrides)
	return SimpleNamespace(**values)


def signed_body(payload: dict) -> tuple[bytes, dict[str, str]]:
	body = json.dumps(payload, separators=(",", ":")).encode()
	return body, {"X-AOS-Analytics-Signature": build_signature(SECRET, body)}


def valid_payload() -> dict:
	return {
		"job_id": "job-1",
		"events": [{"event_type": "view"}],
		"callback_url": "https://callback.invalid/events",
	}


def test_signature_creation_and_verification():
	body = b'{"job_id":"job-1"}'
	signature = build_signature(SECRET, body)
	assert signature.startswith("sha256=")
	assert verify_signature(SECRET, body, signature)
	assert not verify_signature(SECRET, body + b" ", signature)
	assert not verify_signature(SECRET, body, None)
	assert not verify_signature(" ", body, signature)


def test_health_uses_safe_configuration(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json() == {
		"ok": True,
		"service": "aos-analytics-pipeline",
		"environment": "test",
	}


def test_valid_job_uses_queue_boundary(monkeypatch):
	calls = []

	class Queue:
		def fetch_job(self, _job_id):
			return None

		def enqueue(self, *args, **kwargs):
			calls.append((args, kwargs))
			return SimpleNamespace(id="rq-1")

	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_queue", Queue)
	body, headers = signed_body(valid_payload())
	response = TestClient(main.app).post("/events", content=body, headers=headers)
	assert response.status_code == 202
	assert response.json()["service_job_id"] == "rq-1"
	assert calls[0][1]["job_id"] == "job-1"


def test_queue_failure_is_not_reported_as_success(monkeypatch):
	class Queue:
		def fetch_job(self, _job_id):
			return None

		def enqueue(self, *_args, **_kwargs):
			raise RuntimeError("synthetic queue failure")

	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_queue", Queue)
	body, headers = signed_body(valid_payload())
	response = TestClient(main.app, raise_server_exceptions=False).post(
		"/events", content=body, headers=headers
	)
	assert response.status_code == 500


def test_signatures_are_required(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	assert client.post("/events", json=valid_payload()).status_code == 401
	assert (
		client.post(
			"/events",
			json=valid_payload(),
			headers={"X-AOS-Analytics-Signature": "sha256=invalid"},
		).status_code
		== 401
	)


def test_blank_secret_fails_closed(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings(request_secret=" "))
	body, headers = signed_body(valid_payload())
	assert TestClient(main.app).post("/events", content=body, headers=headers).status_code == 401


def test_schema_and_count_bounds(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings(max_events_per_job=1))
	client = TestClient(main.app)

	missing_body, missing_headers = signed_body({"callback_url": "https://callback.invalid"})
	missing = client.post("/events", content=missing_body, headers=missing_headers)
	assert missing.status_code == 422
	assert missing.json()["error"] == "VALIDATION_ERROR"
	assert isinstance(missing.json()["data"]["fields"], list)

	too_long = valid_payload()
	too_long["events"] = [{"event_type": "x" * 121}]
	body, headers = signed_body(too_long)
	assert client.post("/events", content=body, headers=headers).status_code == 422

	too_many = valid_payload()
	too_many["events"] = [{"event_type": "one"}, {"event_type": "two"}]
	body, headers = signed_body(too_many)
	assert client.post("/events", content=body, headers=headers).status_code == 400


def test_operational_metrics_are_private_and_redacted(monkeypatch):
	secret = "metrics-token-secret-0123456789"
	monkeypatch.setenv("AOS_METRICS_ALLOW_LOOPBACK", "false")
	monkeypatch.setenv("AOS_METRICS_TOKEN", secret)
	client = TestClient(main.app)
	denied = client.get("/metrics")
	assert denied.status_code == 403
	response = client.get("/metrics", headers={"Authorization": f"Bearer {secret}"})
	assert response.status_code == 200
	assert "aos_companion_http_requests_total" in response.text
	assert "aos_companion_http_request_duration_seconds" in response.text
	assert "aos_companion_unhandled_exceptions_total" in response.text
	assert "aos_companion_dependency_ready" in response.text
	assert secret not in response.text
	assert "authorization" not in response.text.lower()


def test_unhandled_exception_response_never_leaks_raw_details():
	leaked = "token=do-not-leak /srv/private/internal.py https://internal.invalid"
	path = f"/__observability_failure_{main.app.title.replace(' ', '_').lower()}"

	async def fail_for_test():
		raise RuntimeError(leaked)

	main.app.add_api_route(path, fail_for_test, methods=["GET"], include_in_schema=False)
	response = TestClient(main.app, raise_server_exceptions=False).get(path)
	assert response.status_code == 500
	serialized = response.text
	assert "INTERNAL_ERROR" in serialized
	assert leaked not in serialized
	assert "/srv/private" not in serialized
	assert "internal.invalid" not in serialized
	assert "Traceback" not in serialized


def test_request_validation_error_redacts_sensitive_input(caplog):
	import asyncio

	from app.observability import render_metrics, request_validation_error_handler
	from fastapi.exceptions import RequestValidationError
	from starlette.requests import Request

	sensitive = "Bearer super-secret-token https://internal.invalid /srv/private/key.pem"
	request = Request({"type": "http", "method": "POST", "path": "/jobs", "headers": []})
	exc = RequestValidationError(
		[
			{
				"type": "string_type",
				"loc": ("body", "payload", "url"),
				"msg": "Input should be a valid string",
				"input": sensitive,
				"ctx": {"error": sensitive},
			}
		]
	)
	response = asyncio.run(request_validation_error_handler(request, exc))
	serialized = response.body.decode("utf-8") + caplog.text + render_metrics()
	assert response.status_code == 422
	assert "VALIDATION_ERROR" in serialized
	assert "payload.url" in serialized
	assert sensitive not in serialized
	assert "super-secret-token" not in serialized
	assert "internal.invalid" not in serialized
	assert "/srv/private" not in serialized


def test_idempotent_dispatch_uses_stable_job_id_and_preserves_active_generation(redis_conn, rq_queue):
	from app.idempotent_dispatch import enqueue_idempotent
	from rq import Retry

	payload = {
		"job_id": "service-job-1",
		"idempotency_key": "stable-dispatch-id",
		"dispatch_id": "stable-outbox-id",
		"dispatch_generation": 1,
		"dispatch_token": "a" * 32,
		"callback_url": "https://callback.invalid/result",
	}
	job, action = enqueue_idempotent(
		queue=rq_queue,
		worker_method="app.worker.process",
		payload=payload,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
	)
	assert action == "enqueued"
	assert job.id == "stable-dispatch-id"

	recovery = {**payload, "dispatch_generation": 2, "dispatch_token": "b" * 32}
	job_again, action_again = enqueue_idempotent(
		queue=rq_queue,
		worker_method="app.worker.process",
		payload=recovery,
		job_timeout=300,
		result_ttl=3600,
		failure_ttl=3600,
		retry=Retry(max=1),
	)
	assert job_again.id == job.id
	# The queued older generation has not begun external work and is safely
	# replaced under the same stable work ID.
	assert action_again == "stale_generation_replaced"
	assert rq_queue.fetch_job("stable-dispatch-id").args[0]["dispatch_generation"] == 2


def test_validation_location_normalization_never_echoes_dynamic_keys(caplog):
	import asyncio
	import json as _json

	from app.observability import request_validation_error_handler
	from app.validation_fields import normalize_validation_location
	from fastapi import Request
	from fastapi.exceptions import RequestValidationError

	sensitive_values = [
		"https://internal.invalid/secret",
		"/srv/private/token.txt",
		"Bearer super-secret-token",
		"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJzZWNyZXQifQ.signature",
		"x" * 500,
		"control\x00\nkey",
	]
	for sensitive in sensitive_values:
		field = normalize_validation_location(("body", "payload", "items", 7, sensitive))
		assert field == "payload.items[].field"
		assert sensitive not in field

	nested = normalize_validation_location(("body", "payload", "attacker_key", "nested_key"))
	assert nested == "payload.item.field"

	scope = {
		"type": "http",
		"method": "POST",
		"path": "/jobs",
		"headers": [],
		"query_string": b"",
		"client": ("127.0.0.1", 1),
		"server": ("test", 80),
		"scheme": "http",
	}
	exc = RequestValidationError(
		[
			{
				"type": "value_error",
				"loc": ("body", "payload", "items", 0, sensitive_values[0]),
				"msg": "bad",
				"input": "secret",
			}
		]
	)
	response = asyncio.run(request_validation_error_handler(Request(scope), exc))
	serialized = response.body.decode("utf-8") + caplog.text
	assert response.status_code == 422
	assert "payload.items[].field" in serialized
	for sensitive in sensitive_values:
		assert sensitive not in serialized
	assert "secret" not in _json.dumps(_json.loads(response.body)).lower()


def test_internal_job_status_endpoint_is_signed_and_bounded(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	payload = {"job_id": "service-job-status", "idempotency_key": "stable-status-id"}
	assert client.post("/internal/jobs/status", json=payload).status_code == 401
	body, headers = signed_body(payload)
	response = client.post("/internal/jobs/status", content=body, headers=headers)
	assert response.status_code == 200
	data = response.json()
	assert data["state"] == "absent"
	assert "result_payload" not in data
	assert "dispatch_token" not in data
	assert client.post("/internal/jobs/callback/replay", json=payload).status_code == 401
