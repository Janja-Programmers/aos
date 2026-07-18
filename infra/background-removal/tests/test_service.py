from __future__ import annotations

from io import BytesIO

import pytest
from app import main
from app.config import Settings
from app.image_loader import ImageLoadError, load_image_bytes
from fastapi.testclient import TestClient


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
	monkeypatch.setenv("BACKGROUND_REMOVAL_REQUEST_TIMEOUT_SECONDS", "0")
	monkeypatch.setenv("BACKGROUND_REMOVAL_OUTPUT_FORMAT", "jpeg")
	settings = Settings()
	assert settings.max_image_bytes == 1
	assert settings.request_timeout_seconds == 1.0
	assert settings.output_format == "png"


def test_health_does_not_load_a_model(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["processor_loaded"] is False


def test_valid_upload_uses_processor_boundary(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	response = TestClient(main.app).post(
		"/remove-background",
		files={"image": ("test.png", b"synthetic-image", "image/png")},
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
	)
	assert response.status_code == 503
	assert response.json()["code"] == "BACKGROUND_REMOVAL_UNAVAILABLE"


def test_missing_and_invalid_uploads_are_rejected(monkeypatch):
	monkeypatch.setattr(main, "get_service", FakeService)
	client = TestClient(main.app)
	missing = client.post("/remove-background")
	assert missing.status_code == 422
	assert isinstance(missing.json()["detail"], list)

	wrong_type = client.post(
		"/remove-background",
		files={"image": ("test.txt", b"text", "text/plain")},
	)
	assert wrong_type.status_code == 400


def test_image_size_bound_is_enforced(monkeypatch):
	monkeypatch.setenv("BACKGROUND_REMOVAL_MAX_IMAGE_BYTES", "3")
	settings = Settings()
	assert load_image_bytes(BytesIO(b"abc"), settings=settings) == b"abc"
	with pytest.raises(ImageLoadError, match="maximum size"):
		load_image_bytes(BytesIO(b"abcd"), settings=settings)
