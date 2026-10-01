from __future__ import annotations

import pytest
from app import main
from app.translator import TranslationConfig, TranslatorRuntime, ValidationError, get_config
from fastapi.testclient import TestClient

AUTH = {"X-AOS-Internal-Token": "unit-test-translation-token"}


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
		headers=AUTH,
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
		headers=AUTH,
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
	missing = client.post("/translate", json={"target_language": "en"}, headers=AUTH)
	assert missing.status_code == 422
	assert missing.json()["error"] == "VALIDATION_ERROR"
	assert isinstance(missing.json()["data"]["fields"], list)

	invalid_type = client.post(
		"/translate",
		json={"text": ["wrong"], "source_language": "en", "target_language": "en"},
		headers=AUTH,
	)
	assert invalid_type.status_code == 422


def test_languages_endpoint_is_deterministic():
	response = TestClient(main.app).get("/languages", headers=AUTH)
	assert response.status_code == 200
	labels = [item["label"] for item in response.json()["items"]]
	assert labels == sorted(labels)
	assert "English" in labels
	assert "German" in labels
	german = next(item for item in response.json()["items"] if item["label"] == "German")
	assert german["code"] == "deu_Latn"


def test_private_translation_routes_fail_closed_without_valid_internal_token(monkeypatch):
	client = TestClient(main.app)
	missing = client.get("/languages")
	assert missing.status_code == 401
	wrong = client.post(
		"/translate",
		json={"text": "hello", "source_language": "en", "target_language": "de"},
		headers={"X-AOS-Internal-Token": "wrong"},
	)
	assert wrong.status_code == 401
	monkeypatch.delenv("TRANSLATION_INTERNAL_TOKEN", raising=False)
	unconfigured = client.get("/languages", headers=AUTH)
	assert unconfigured.status_code == 503


def test_german_alias_normalizes_without_loading_model():
	runtime = TranslatorRuntime(config())
	result = runtime.translate(text="Hallo", source_language="de", target_language="de")
	assert result.source_language == "deu_Latn"
	assert result.target_language == "deu_Latn"
	assert result.target_language_label == "German"
	assert runtime.is_loaded is False


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
