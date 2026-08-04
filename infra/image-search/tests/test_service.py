from __future__ import annotations

import pytest
from app import main
from app.config import Settings
from app.schemas import ImageReference
from fastapi.testclient import TestClient
from pydantic import ValidationError


class FakeService:
	def health(self):
		return {
			"ok": True,
			"service": "aos-image-search",
			"mode": "cpu",
			"model_loaded": False,
			"vector_store_ready": False,
			"config": {"environment": "test"},
		}

	def ready(self):
		return {
			"ok": True,
			"service": "aos-image-search",
			"mode": "cpu",
			"ready": True,
			"model_loaded": False,
			"vector_store_ready": True,
			"config": {"environment": "test"},
		}

	def replace_ad_images(self, *, ad_id, images):
		return {"ok": True, "ad_id": ad_id, "indexed_count": len(images), "failed_count": 0}


def test_configuration_defaults_and_limits(monkeypatch):
	monkeypatch.setenv("IMAGE_SEARCH_LIMIT", "0")
	monkeypatch.setenv("IMAGE_SEARCH_MAX_LIMIT", "3")
	monkeypatch.setenv("IMAGE_SEARCH_SCORE_THRESHOLD", "-2")
	settings = Settings()
	assert settings.default_limit == 1
	assert settings.clamp_limit(99) == 3
	assert settings.score_threshold == 0.0


def test_health_does_not_load_or_download_a_model(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["model_loaded"] is False


def test_replace_images_uses_service_boundary(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).post(
		"/ads/AD-1/replace-images",
		json={"images": [{"image_url": "/files/synthetic.png", "sort_order": 0}]},
	)
	assert response.status_code == 200
	assert response.json()["indexed_count"] == 1


def test_service_failure_is_not_reported_as_success(monkeypatch):
	class FailingService(FakeService):
		def replace_ad_images(self, **_kwargs):
			raise main.VectorStoreError("synthetic vector-store failure")

	monkeypatch.setattr(main, "get_service", FailingService)
	response = TestClient(main.app).post(
		"/ads/AD-1/replace-images",
		json={"images": [{"image_url": "/files/synthetic.png", "sort_order": 0}]},
	)
	assert response.status_code == 503


def test_request_schema_rejects_missing_and_invalid_fields(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	client = TestClient(main.app)
	missing = client.post("/ads/AD-1/replace-images", json={"images": [{}]})
	assert missing.status_code == 422
	assert missing.json()["error"] == "VALIDATION_ERROR"
	assert isinstance(missing.json()["data"]["fields"], list)

	invalid = client.post(
		"/ads/AD-1/replace-images",
		json={"images": [{"image_url": "/files/synthetic.png", "sort_order": -1}]},
	)
	assert invalid.status_code == 422

	with pytest.raises(ValidationError):
		ImageReference(image_url="", sort_order=0)


def test_image_upload_validation_happens_before_service_call(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	client = TestClient(main.app)
	assert client.post("/search/image").status_code == 422
	response = client.post(
		"/search/image",
		files={"image": ("test.txt", b"text", "text/plain")},
	)
	assert response.status_code == 400


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


def test_short_frame_classification_requires_signature(monkeypatch):
	from types import SimpleNamespace

	monkeypatch.setattr(
		main,
		"get_settings",
		lambda: SimpleNamespace(
			internal_secret="classification-secret",
			max_image_bytes=1048576,
			short_classification_max_frames=6,
			short_classification_max_frame_bytes=1048576,
			short_classification_max_total_bytes=6291456,
		),
	)
	response = TestClient(main.app).post(
		"/internal/shorts/classify-frames",
		json={"frames": ["aW52YWxpZA=="]},
	)
	assert response.status_code == 401


def test_short_frame_classification_uses_signed_internal_boundary(monkeypatch):
	import base64
	import json
	from io import BytesIO
	from types import SimpleNamespace

	from PIL import Image
	from app.security import build_signature

	secret = "classification-secret"
	settings = SimpleNamespace(
		internal_secret=secret,
		max_image_bytes=1048576,
		short_classification_max_frames=6,
		short_classification_max_frame_bytes=1048576,
		short_classification_max_total_bytes=6291456,
	)
	monkeypatch.setattr(main, "get_settings", lambda: settings)

	class FakeClassifier:
		def classify(self, images):
			assert len(images) == 1
			return {
				"status": "ready",
				"mode": "learn",
				"confidence": 0.9,
				"margin": 0.6,
				"scores": {"shop": 0.02, "geo": 0.03, "vibes": 0.05, "learn": 0.9},
				"model": "synthetic",
				"model_version": "test-v1",
				"frame_count": 1,
			}

	monkeypatch.setattr(main, "get_short_classifier", lambda: FakeClassifier())
	buffer = BytesIO()
	Image.new("RGB", (8, 8)).save(buffer, format="JPEG")
	body = json.dumps(
		{"frames": [base64.b64encode(buffer.getvalue()).decode("ascii")]},
		separators=(",", ":"),
		sort_keys=True,
	).encode("utf-8")
	timestamp = "1700000000"
	monkeypatch.setattr(main.time, "time", lambda: float(timestamp))
	signed_payload = timestamp.encode("ascii") + b"." + body
	response = TestClient(main.app).post(
		"/internal/shorts/classify-frames",
		content=body,
		headers={
			"Content-Type": "application/json",
			"X-AOS-Timestamp": timestamp,
			"X-AOS-Signature": build_signature(secret, signed_payload),
		},
	)
	assert response.status_code == 200
	assert response.json()["mode"] == "learn"


def test_short_frame_classification_rejects_stale_signature(monkeypatch):
	from types import SimpleNamespace

	from app.security import build_signature

	secret = "classification-secret"
	monkeypatch.setattr(
		main,
		"get_settings",
		lambda: SimpleNamespace(
			internal_secret=secret,
			max_image_bytes=1048576,
			short_classification_max_frames=6,
			short_classification_max_frame_bytes=1048576,
			short_classification_max_total_bytes=6291456,
		),
	)
	monkeypatch.setattr(main.time, "time", lambda: 1700001000.0)
	body = b'{"frames":["aW52YWxpZA=="]}'
	timestamp = "1700000000"
	signature = build_signature(secret, timestamp.encode("ascii") + b"." + body)
	response = TestClient(main.app).post(
		"/internal/shorts/classify-frames",
		content=body,
		headers={"X-AOS-Timestamp": timestamp, "X-AOS-Signature": signature},
	)
	assert response.status_code == 401
