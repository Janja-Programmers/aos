from __future__ import annotations

import json
from types import SimpleNamespace

from app import config, main
from app.security import build_signature, verify_signature
from fastapi.testclient import TestClient

SECRET = "unit-test-secret"


def settings(**overrides):
	values = {
		"service_name": "aos-video-processing",
		"environment": "test",
		"request_secret": SECRET,
		"queue_name": "video",
		"job_timeout_seconds": 600,
		"result_ttl_seconds": 60,
		"failure_ttl_seconds": 60,
		"callback_allowed_hosts": (),
	}
	values.update(overrides)
	return SimpleNamespace(**values)


def payload() -> dict:
	return {
		"job_id": "job-1",
		"short_id": "SHORT-2026-00001",
		"callback_url": "https://callback.invalid/video",
		"raw_video": {"bucket": "synthetic", "object_key": "clip.mp4", "size_bytes": 1024},
		"output": {
			"output_bucket": "shorts",
			"output_base_path": "shorts/processed",
			"thumbnail_bucket": "aos-public",
			"thumbnail_base_path": "shorts/thumbnails",
			"max_duration_seconds": 180,
		},
	}


def signed(data: dict) -> tuple[bytes, dict[str, str]]:
	body = json.dumps(data, separators=(",", ":")).encode()
	return body, {"X-AOS-Signature": build_signature(SECRET, body)}


def test_configuration_bounds(monkeypatch):
	monkeypatch.setenv("VIDEO_JOB_TIMEOUT_SECONDS", "1")
	monkeypatch.setenv("VIDEO_MAX_DURATION_SECONDS", "0")
	assert config._int("VIDEO_JOB_TIMEOUT_SECONDS", 1800, min_value=60) == 60
	assert config._int("VIDEO_MAX_DURATION_SECONDS", 180, min_value=1) == 1


def test_signature_checks():
	body = b"payload"
	signature = build_signature(SECRET, body)
	assert verify_signature(SECRET, body, signature)
	assert not verify_signature(SECRET, b"other", signature)
	assert not verify_signature(SECRET, body, None)
	assert not verify_signature("", body, signature)


def test_health(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["service"] == "aos-video-processing"


def test_valid_job_is_enqueued(monkeypatch):
	calls = []

	class Queue:
		def fetch_job(self, _job_id):
			return None

		def enqueue(self, *args, **kwargs):
			calls.append((args, kwargs))
			return SimpleNamespace(id="rq-1")

	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_queue", Queue)
	body, headers = signed(payload())
	response = TestClient(main.app).post("/jobs", content=body, headers=headers)
	assert response.status_code == 202
	assert response.json()["service_job_id"] == "rq-1"
	assert calls[0][1]["job_id"] == "job-1"


def test_queue_failure_returns_server_error(monkeypatch):
	class Queue:
		def fetch_job(self, _job_id):
			return None

		def enqueue(self, *_args, **_kwargs):
			raise RuntimeError("synthetic queue failure")

	monkeypatch.setattr(main, "get_settings", lambda: settings())
	monkeypatch.setattr(main, "get_queue", Queue)
	body, headers = signed(payload())
	response = TestClient(main.app, raise_server_exceptions=False).post(
		"/jobs", content=body, headers=headers
	)
	assert response.status_code == 500


def test_signature_and_schema_validation(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	assert client.post("/jobs", json=payload()).status_code == 401

	invalid = payload()
	invalid["force"] = "not-a-boolean"
	body, headers = signed(invalid)
	response = client.post("/jobs", content=body, headers=headers)
	assert response.status_code == 422
	assert response.json()["error"] == "VALIDATION_ERROR"
	assert isinstance(response.json()["data"]["fields"], list)

	missing = payload()
	missing.pop("short_id")
	body, headers = signed(missing)
	assert client.post("/jobs", content=body, headers=headers).status_code == 422


def test_blank_secret_fails_closed(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings(request_secret=""))
	body, headers = signed(payload())
	assert TestClient(main.app).post("/jobs", content=body, headers=headers).status_code == 401


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
	body, headers = signed(payload)
	response = client.post("/internal/jobs/status", content=body, headers=headers)
	assert response.status_code == 200
	data = response.json()
	assert data["state"] == "absent"
	assert "result_payload" not in data
	assert "dispatch_token" not in data
	assert client.post("/internal/jobs/callback/replay", json=payload).status_code == 401


def test_video_job_request_accepts_audio_reprocess_reason():
	from app.main import VideoJobRequest

	request = VideoJobRequest.model_validate(
		{
			"job_id": "VIDEO-JOB-2026-00001",
			"idempotency_key": "abcdefgh",
			"short_id": "SHORT-2026-00001",
			"force": True,
			"reason": "audio_reprocess",
			"callback_url": "https://callback.invalid/video",
			"raw_video": {
				"bucket": "raw",
				"object_key": "shorts/raw/clip.mp4",
				"size_bytes": 1024,
			},
			"sound": {
				"bucket": "public",
				"object_key": "sounds/uploads/track.mp3",
				"volume": 0.8,
			},
			"output": {
				"output_bucket": "shorts",
				"output_base_path": "shorts/processed",
				"thumbnail_bucket": "public",
				"thumbnail_base_path": "shorts/thumbnails",
				"max_duration_seconds": 600,
			},
		}
	)

	assert request.reason == "audio_reprocess"
