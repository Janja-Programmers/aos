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
	}
	values.update(overrides)
	return SimpleNamespace(**values)


def payload() -> dict:
	return {
		"job_id": "job-1",
		"short_id": "SHORT-1",
		"callback_url": "https://callback.invalid/video",
		"raw_video": {"bucket": "synthetic", "object_key": "clip.mp4"},
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
	assert isinstance(response.json()["detail"], list)

	missing = payload()
	missing.pop("short_id")
	body, headers = signed(missing)
	assert client.post("/jobs", content=body, headers=headers).status_code == 422


def test_blank_secret_fails_closed(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings(request_secret=""))
	body, headers = signed(payload())
	assert TestClient(main.app).post("/jobs", content=body, headers=headers).status_code == 401
