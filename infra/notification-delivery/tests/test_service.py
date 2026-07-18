from __future__ import annotations

import json
from types import SimpleNamespace

from app import config, main
from app.security import build_signature, verify_signature
from fastapi.testclient import TestClient

SECRET = "unit-test-secret"


def settings(**overrides):
	values = {
		"service_name": "aos-notification-delivery",
		"environment": "test",
		"request_secret": SECRET,
		"queue_name": "notification-delivery",
		"job_timeout_seconds": 600,
		"result_ttl_seconds": 60,
		"failure_ttl_seconds": 60,
	}
	values.update(overrides)
	return SimpleNamespace(**values)


def payload() -> dict:
	return {
		"job_id": "job-1",
		"user": "test-user@example.invalid",
		"callback_url": "https://callback.invalid/notification",
		"tokens": [],
	}


def signed(data: dict) -> tuple[bytes, dict[str, str]]:
	body = json.dumps(data, separators=(",", ":")).encode()
	return body, {"X-AOS-Notification-Signature": build_signature(SECRET, body)}


def test_configuration_defaults_and_invalid_integer(monkeypatch):
	monkeypatch.setenv("NOTIFICATION_MAX_TOKENS_PER_MULTICAST", "invalid")
	assert config._int("NOTIFICATION_MAX_TOKENS_PER_MULTICAST", 500) == 500
	assert config.Settings().queue_name == "notification-delivery"


def test_signature_checks():
	body = b"payload"
	signature = build_signature(SECRET, body)
	assert verify_signature(SECRET, body, signature)
	assert not verify_signature(SECRET, b"other", signature)
	assert not verify_signature(SECRET, body, None)
	assert not verify_signature(" ", body, signature)


def test_health(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["environment"] == "test"


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


def test_signature_and_request_validation(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings())
	client = TestClient(main.app)
	assert client.post("/jobs", json=payload()).status_code == 401

	body, headers = signed({"job_id": "job-1", "user": 42, "callback_url": "x"})
	response = client.post("/jobs", content=body, headers=headers)
	assert response.status_code == 422
	assert isinstance(response.json()["detail"], list)


def test_blank_secret_fails_closed(monkeypatch):
	monkeypatch.setattr(main, "get_settings", lambda: settings(request_secret=""))
	body, headers = signed(payload())
	assert TestClient(main.app).post("/jobs", content=body, headers=headers).status_code == 401
