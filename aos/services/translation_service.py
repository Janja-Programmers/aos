"""Translation service client.

Calls the internal AOS Translation Docker service.

Responsibilities:
- Read translation settings from AOS Settings snapshot.
- Validate text length before calling the model service.
- Call POST /translate.
- Normalize and validate the response shape.
- Convert connection/timeouts/provider failures into clear application errors.

The actual ML model does NOT run inside Frappe.
It runs in the external translation service container.
"""

from __future__ import annotations

from typing import Any, Dict

import requests
from aos.utils.aos_settings import get_aos_settings_snapshot


class TranslationServiceError(Exception):
	"""Base exception for translation service failures."""


class TranslationValidationError(TranslationServiceError):
	"""Raised when the translation request is invalid."""


class TranslationUnavailableError(TranslationServiceError):
	"""Raised when the translation service is unreachable or fails."""


DEFAULT_SOURCE_LANGUAGE = "eng_Latn"
DEFAULT_PROVIDER = "nllb"
DEFAULT_MODEL_NAME = "nllb-200-distilled-1.3B-ct2-int8"


def _clean_text(value: str | None) -> str:
	text = (value or "").strip()

	if not text:
		raise TranslationValidationError("Text is required for translation.")

	return text


def _clean_language(value: str | None, *, fieldname: str) -> str:
	language = (value or "").strip()

	if not language:
		raise TranslationValidationError(f"{fieldname} is required.")

	return language


def _build_translate_url(base_url: str) -> str:
	return f"{base_url.rstrip('/')}/translate"


def _parse_json_response(response: requests.Response) -> Dict[str, Any]:
	try:
		payload = response.json()
	except Exception:
		raise TranslationUnavailableError(
			"Translation service returned an invalid response."
		)

	if not isinstance(payload, dict):
		raise TranslationUnavailableError(
			"Translation service returned an invalid payload."
		)

	return payload


def _extract_error_message(payload: Dict[str, Any]) -> str:
	detail = payload.get("detail")

	if isinstance(detail, str) and detail.strip():
		return detail.strip()

	if isinstance(detail, dict):
		message = detail.get("message") or detail.get("error")
		if isinstance(message, str) and message.strip():
			return message.strip()

	return "Translation service failed."


def _validate_translation_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
	translated_content = (payload.get("translated_content") or "").strip()

	if not translated_content:
		raise TranslationUnavailableError(
			"Translation service returned empty translated content."
		)

	source_language = (payload.get("source_language") or DEFAULT_SOURCE_LANGUAGE).strip()
	target_language = (payload.get("target_language") or "").strip()

	if not target_language:
		raise TranslationUnavailableError(
			"Translation service returned an invalid target language."
		)

	return {
		"source_language": source_language,
		"target_language": target_language,
		"source_language_label": (
			(payload.get("source_language_label") or source_language).strip()
		),
		"target_language_label": (
			(payload.get("target_language_label") or target_language).strip()
		),
		"translated_content": translated_content,
		"provider": (payload.get("provider") or DEFAULT_PROVIDER).strip(),
		"model_name": (payload.get("model_name") or DEFAULT_MODEL_NAME).strip(),
	}


def translate_text(
	*,
	text: str,
	target_language: str,
	source_language: str | None = None,
) -> Dict[str, Any]:
	"""Translate text using the configured internal translation service.

	Args:
	    text: Source text to translate.
	    target_language: Target language code. Can be simple code like "sw"
	        or normalized NLLB code like "swh_Latn".
	    source_language: Optional source language code. If omitted, the
	        translation service uses its configured default source language.

	Returns:
	    {
	        "source_language": "eng_Latn",
	        "target_language": "swh_Latn",
	        "source_language_label": "English",
	        "target_language_label": "Swahili",
	        "translated_content": "...",
	        "provider": "nllb",
	        "model_name": "nllb-200-distilled-1.3B-ct2-int8",
	    }

	Raises:
	    TranslationValidationError
	    TranslationUnavailableError
	"""

	settings = get_aos_settings_snapshot()

	clean = _clean_text(text)
	target = _clean_language(target_language, fieldname="target_language")
	source = (source_language or "").strip() or None

	if len(clean) > settings.translation_max_characters:
		raise TranslationValidationError(
			f"Text cannot exceed {settings.translation_max_characters} characters."
		)

	url = _build_translate_url(settings.translation_service_url)

	request_payload: Dict[str, Any] = {
		"text": clean,
		"target_language": target,
	}

	if source:
		request_payload["source_language"] = source

	try:
		response = requests.post(
			url,
			json=request_payload,
			timeout=settings.translation_service_timeout_seconds,
		)

	except requests.Timeout:
		raise TranslationUnavailableError(
			"Translation service timed out. Please try again."
		)

	except requests.RequestException:
		raise TranslationUnavailableError(
			"Translation service is unavailable. Please try again."
		)

	payload = _parse_json_response(response)

	if response.status_code >= 400:
		raise TranslationUnavailableError(_extract_error_message(payload))

	return _validate_translation_payload(payload)


def health_check() -> Dict[str, Any]:
	"""Check the configured translation service health endpoint."""

	settings = get_aos_settings_snapshot()
	url = f"{settings.translation_service_url.rstrip('/')}/health"

	try:
		response = requests.get(
			url,
			timeout=settings.translation_service_timeout_seconds,
		)

	except requests.Timeout:
		raise TranslationUnavailableError("Translation service health check timed out.")

	except requests.RequestException:
		raise TranslationUnavailableError("Translation service is unavailable.")

	payload = _parse_json_response(response)

	if response.status_code >= 400:
		raise TranslationUnavailableError(_extract_error_message(payload))

	return payload
