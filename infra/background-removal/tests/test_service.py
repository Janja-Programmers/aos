from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from app import main, model_artifact
from app.config import Settings
from app.image_loader import ImageLoadError, load_image_bytes
from app.model_artifact import ModelArtifactError, ModelManifest
from app.processor import BackgroundRemovalBusyError, BackgroundRemovalProcessor
from fastapi.testclient import TestClient

AUTH = {"Authorization": "Bearer test-background-removal-secret-0123456789"}


class FakeService:
	def health(self):
		return {
			"ok": True,
			"service": "aos-background-removal",
			"mode": "cpu",
			"processor_loaded": False,
			"config": {"environment": "test"},
		}

	def ready(self):
		return {
			"ok": True,
			"service": "aos-background-removal",
			"mode": "cpu",
			"ready": True,
			"processor_loaded": True,
			"config": {"environment": "test"},
		}

	def remove_background(self, **_kwargs):
		return b"synthetic-png"


def test_configuration_defaults_and_bounds(monkeypatch):
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_IMAGE_BYTES", "0")
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_IMAGE_PIXELS", "999999999")
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_CONCURRENT_INFERENCES", "0")
	monkeypatch.setenv("BACKGROUND_REMOVAL_INFERENCE_ACQUIRE_TIMEOUT_SECONDS", "999")
	settings = Settings()
	assert settings.max_image_bytes == 1
	assert settings.max_image_pixels == 40_000_000
	assert settings.max_concurrent_inferences == 1
	assert settings.inference_acquire_timeout_seconds == 30.0


def test_health_does_not_load_a_model(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["processor_loaded"] is False


def test_ready_warms_the_processor_boundary(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).get("/ready")
	assert response.status_code == 200
	assert response.json()["ready"] is True
	assert response.json()["processor_loaded"] is True


def test_remove_background_requires_internal_bearer_secret(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	client = TestClient(main.app)
	missing = client.post(
		"/remove-background",
		files={"image": ("test.png", b"synthetic-image", "image/png")},
	)
	assert missing.status_code == 401
	wrong = client.post(
		"/remove-background",
		files={"image": ("test.png", b"synthetic-image", "image/png")},
		headers={"Authorization": "Bearer wrong-secret"},
	)
	assert wrong.status_code == 401


def test_oversized_request_is_rejected_before_upload_parsing(monkeypatch):
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_IMAGE_BYTES", "1024")
	main.get_settings.cache_clear()
	try:
		response = TestClient(main.app).post(
			"/remove-background",
			content=b"not-even-multipart",
			headers={**AUTH, "Content-Length": str(2 * 1024 * 1024)},
		)
		assert response.status_code == 413
		assert response.json()["code"] == "FILE_TOO_LARGE"
	finally:
		main.get_settings.cache_clear()


def test_valid_upload_uses_processor_boundary(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).post(
		"/remove-background",
		files={"image": ("test.png", b"synthetic-image", "image/png")},
		headers=AUTH,
	)
	assert response.status_code == 200
	assert response.content == b"synthetic-png"
	assert response.headers["x-aos-output-format"] == "png"


def test_processor_failure_is_returned_as_a_safe_service_error(monkeypatch):
	class FailingService(FakeService):
		def remove_background(self, **_kwargs):
			raise main.BackgroundRemovalRuntimeError("synthetic processor failure")

	monkeypatch.setattr(main, "get_service", FailingService)
	response = TestClient(main.app).post(
		"/remove-background",
		files={"image": ("test.png", b"synthetic-image", "image/png")},
		headers=AUTH,
	)
	assert response.status_code == 503
	assert response.json()["code"] == "BACKGROUND_REMOVAL_UNAVAILABLE"


def test_busy_processor_is_retryable(monkeypatch):
	class BusyService(FakeService):
		def remove_background(self, **_kwargs):
			raise BackgroundRemovalBusyError("capacity saturated")

	monkeypatch.setattr(main, "get_service", BusyService)
	response = TestClient(main.app).post(
		"/remove-background",
		files={"image": ("test.png", b"synthetic-image", "image/png")},
		headers=AUTH,
	)
	assert response.status_code == 503
	assert response.json()["code"] == "BACKGROUND_REMOVAL_BUSY"
	assert response.headers["retry-after"] == "1"


def test_inference_capacity_is_bounded_before_model_execution(monkeypatch):
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_CONCURRENT_INFERENCES", "1")
	monkeypatch.setenv("BACKGROUND_REMOVAL_INFERENCE_ACQUIRE_TIMEOUT_SECONDS", "0")
	processor = BackgroundRemovalProcessor(Settings())
	assert processor._inference_slots.acquire(blocking=False) is True
	try:
		with pytest.raises(BackgroundRemovalBusyError, match="capacity is saturated"):
			processor.remove_background(b"synthetic-image")
	finally:
		processor._inference_slots.release()


def test_missing_and_invalid_uploads_are_rejected(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	client = TestClient(main.app)
	missing = client.post("/remove-background", headers=AUTH)
	assert missing.status_code == 422
	assert missing.json()["error"] == "VALIDATION_ERROR"
	assert isinstance(missing.json()["data"]["fields"], list)

	wrong_type = client.post(
		"/remove-background",
		files={"image": ("test.txt", b"text", "text/plain")},
		headers=AUTH,
	)
	assert wrong_type.status_code == 400


def test_image_size_bound_is_enforced(monkeypatch):
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_IMAGE_BYTES", "3")
	settings = Settings()
	assert load_image_bytes(BytesIO(b"abc"), settings=settings) == b"abc"
	with pytest.raises(ImageLoadError, match="maximum size"):
		load_image_bytes(BytesIO(b"abcd"), settings=settings)


def _model_manifest_for(payload: bytes) -> ModelManifest:
	return ModelManifest(
		name="u2net",
		filename="u2net.onnx",
		relative_path="models/u2net/u2net.onnx",
		url="https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx",
		sha256=hashlib.sha256(payload).hexdigest(),
		upstream_md5="0" * 32,
	)


def _write_model(root: Path, payload: bytes, *, mode: int = 0o444) -> Path:
	path = root / "models" / "u2net" / "u2net.onnx"
	path.parent.mkdir(parents=True)
	path.write_bytes(payload)
	path.chmod(mode)
	return path


def test_verified_model_artifact_requires_exact_read_only_bytes(monkeypatch, tmp_path):
	payload = b"reviewed-u2net-model"
	root = tmp_path / "rembg"
	path = _write_model(root, payload)
	monkeypatch.setenv("REMBG_HOME", str(root))
	monkeypatch.setattr(model_artifact, "get_model_manifest", lambda: _model_manifest_for(payload))

	verified = model_artifact.verify_model_artifact()
	assert verified == path.resolve()


def test_model_artifact_rejects_writable_model(monkeypatch, tmp_path):
	payload = b"reviewed-u2net-model"
	root = tmp_path / "rembg"
	_write_model(root, payload, mode=0o644)
	monkeypatch.setenv("REMBG_HOME", str(root))
	monkeypatch.setattr(model_artifact, "get_model_manifest", lambda: _model_manifest_for(payload))

	with pytest.raises(ModelArtifactError, match="read-only"):
		model_artifact.verify_model_artifact()


def test_model_artifact_rejects_checksum_mismatch(monkeypatch, tmp_path):
	root = tmp_path / "rembg"
	_write_model(root, b"tampered-model")
	monkeypatch.setenv("REMBG_HOME", str(root))
	monkeypatch.setattr(model_artifact, "get_model_manifest", lambda: _model_manifest_for(b"reviewed-model"))

	with pytest.raises(ModelArtifactError, match="checksum"):
		model_artifact.verify_model_artifact()


def test_model_artifact_rejects_symlink(monkeypatch, tmp_path):
	payload = b"reviewed-u2net-model"
	root = tmp_path / "rembg"
	model_dir = root / "models" / "u2net"
	model_dir.mkdir(parents=True)
	target = tmp_path / "real-u2net.onnx"
	target.write_bytes(payload)
	target.chmod(0o444)
	(model_dir / "u2net.onnx").symlink_to(target)
	monkeypatch.setenv("REMBG_HOME", str(root))
	monkeypatch.setattr(model_artifact, "get_model_manifest", lambda: _model_manifest_for(payload))

	with pytest.raises(ModelArtifactError, match="symlink"):
		model_artifact.verify_model_artifact()


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
