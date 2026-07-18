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
	assert isinstance(missing.json()["detail"], list)

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
