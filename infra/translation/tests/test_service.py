from __future__ import annotations

import pytest
from app import main
from app.translator import TranslationConfig, TranslatorRuntime, ValidationError, get_config
from fastapi.testclient import TestClient


def config(*, max_chars=1000):
	return TranslationConfig(
		model_path="/nonexistent/unit-test-model",
		model_name="synthetic-model",
		device="cpu",
		compute_type="int8",
		default_source_language="eng_Latn",
		max_chars=max_chars,
	)


def test_configuration_parsing(monkeypatch):
	monkeypatch.setenv("TRANSLATION_MODEL_PATH", "/tmp/synthetic-model")
	monkeypatch.setenv("TRANSLATION_MAX_CHARS", "321")
	settings = get_config()
	assert settings.model_path == "/tmp/synthetic-model"
	assert settings.max_chars == 321


def test_health_does_not_initialize_or_download_model(monkeypatch):
	monkeypatch.setattr(main, "get_config", config)
	response = TestClient(main.app).get("/health")
	assert response.status_code == 200
	assert response.json()["model_name"] == "synthetic-model"


def test_same_language_translation_does_not_load_model():
	runtime = TranslatorRuntime(config())
	result = runtime.translate(text="  hello  ", source_language="en", target_language="en")
	assert result.translated_content == "hello"
	assert result.source_language == "eng_Latn"
	assert runtime.is_loaded is False


def test_translate_endpoint_uses_runtime_boundary(monkeypatch):
	monkeypatch.setattr(main, "get_runtime", lambda: TranslatorRuntime(config()))
	response = TestClient(main.app).post(
		"/translate",
		json={"text": "  hello  ", "source_language": "en", "target_language": "en"},
	)
	assert response.status_code == 200
	assert response.json()["translated_content"] == "hello"


def test_translate_runtime_failure_is_not_reported_as_success(monkeypatch):
	class FailingRuntime:
		def translate(self, **_kwargs):
			raise main.TranslationError("synthetic provider failure")

	monkeypatch.setattr(main, "get_runtime", FailingRuntime)
	response = TestClient(main.app).post(
		"/translate",
		json={"text": "hello", "source_language": "en", "target_language": "fr"},
	)
	assert response.status_code == 500


def test_text_and_language_bounds():
	runtime = TranslatorRuntime(config(max_chars=3))
	with pytest.raises(ValidationError, match="required"):
		runtime.translate(text=" ", source_language="en", target_language="en")
	with pytest.raises(ValidationError, match="cannot exceed"):
		runtime.translate(text="four", source_language="en", target_language="en")
	with pytest.raises(ValidationError, match="Unsupported target"):
		runtime.translate(text="ok", source_language="en", target_language="unsupported")


def test_request_schema_validation(monkeypatch):
	monkeypatch.setattr(main, "get_runtime", lambda: TranslatorRuntime(config()))
	client = TestClient(main.app)
	missing = client.post("/translate", json={"target_language": "en"})
	assert missing.status_code == 422
	assert isinstance(missing.json()["detail"], list)

	invalid_type = client.post(
		"/translate",
		json={"text": ["wrong"], "source_language": "en", "target_language": "en"},
	)
	assert invalid_type.status_code == 422


def test_languages_endpoint_is_deterministic():
	response = TestClient(main.app).get("/languages")
	assert response.status_code == 200
	labels = [item["label"] for item in response.json()["items"]]
	assert labels == sorted(labels)
	assert "English" in labels
