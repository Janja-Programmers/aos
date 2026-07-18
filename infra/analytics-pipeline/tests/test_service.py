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
	assert isinstance(missing.json()["detail"], list)

	too_long = valid_payload()
	too_long["events"] = [{"event_type": "x" * 121}]
	body, headers = signed_body(too_long)
	assert client.post("/events", content=body, headers=headers).status_code == 422

	too_many = valid_payload()
	too_many["events"] = [{"event_type": "one"}, {"event_type": "two"}]
	body, headers = signed_body(too_many)
	assert client.post("/events", content=body, headers=headers).status_code == 400
