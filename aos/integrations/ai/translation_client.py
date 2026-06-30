"""Translation service client.

Calls the internal AOS Translation Docker service.

Responsibilities:
- Read translation business limits from AOS Settings and service URL from env.
- Validate request size before calling the model service.
- Call the external FastAPI service over HTTP.
- Normalize and validate response shapes.
- Convert connection/timeouts/service failures into clear application errors.

The actual translation model, provider, tokenizer, and runtime do NOT run inside
Frappe. They run in the external translation service container.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import requests

from aos.utils.aos_config import get_translation_service_url
from aos.utils.aos_settings import get_aos_settings_snapshot


class TranslationServiceError(Exception):
    """Base exception for translation integration failures."""


class TranslationValidationError(TranslationServiceError):
    """Raised when the translation request is invalid before calling the service."""


class TranslationUnavailableError(TranslationServiceError):
    """Raised when the translation service is unreachable or returns an invalid response."""


DEFAULT_SERVICE_URL = "http://127.0.0.1:8100"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_CHARACTERS = 1000
MAX_CHARACTERS = 5000


@dataclass(frozen=True)
class TranslationClientSettings:
    service_url: str
    timeout_seconds: int
    max_characters: int


def _clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        number = int(value)
    except Exception:
        number = int(default)

    if number < min_value:
        return int(min_value)
    if number > max_value:
        return int(max_value)
    return int(number)


def _clean_url(value: Any, default: str = DEFAULT_SERVICE_URL) -> str:
    url = str(value or "").strip()
    if not url:
        url = default
    return url.rstrip("/")


def get_translation_client_settings() -> TranslationClientSettings:
    """Resolve integration settings from AOS Settings snapshot.

    Model/runtime/provider settings intentionally do not appear here. They belong
    to the external translation service, not the Frappe business backend.
    """

    try:
        settings = get_aos_settings_snapshot()
    except Exception:
        # Defensive fallback for early install/migration contexts where the
        # singleton may not be available yet.
        return TranslationClientSettings(
            service_url=DEFAULT_SERVICE_URL,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            max_characters=DEFAULT_MAX_CHARACTERS,
        )

    return TranslationClientSettings(
        service_url=_clean_url(
            get_translation_service_url(),
            default=DEFAULT_SERVICE_URL,
        ),
        timeout_seconds=_clamp_int(
            getattr(settings, "translation_service_timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
            default=DEFAULT_TIMEOUT_SECONDS,
            min_value=1,
            max_value=60,
        ),
        max_characters=_clamp_int(
            getattr(settings, "translation_max_characters", DEFAULT_MAX_CHARACTERS),
            default=DEFAULT_MAX_CHARACTERS,
            min_value=1,
            max_value=MAX_CHARACTERS,
        ),
    )


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

    message = payload.get("message") or payload.get("error")
    if isinstance(message, str) and message.strip():
        return message.strip()

    return "Translation service failed."


def _validate_translation_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    translated_content = str(payload.get("translated_content") or "").strip()
    if not translated_content:
        raise TranslationUnavailableError(
            "Translation service returned empty translated content."
        )

    source_language = str(payload.get("source_language") or "").strip()
    target_language = str(payload.get("target_language") or "").strip()

    if not source_language:
        raise TranslationUnavailableError(
            "Translation service returned an invalid source language."
        )

    if not target_language:
        raise TranslationUnavailableError(
            "Translation service returned an invalid target language."
        )

    provider = str(payload.get("provider") or "").strip() or None
    model_name = str(payload.get("model_name") or "").strip() or None

    return {
        "source_language": source_language,
        "target_language": target_language,
        "source_language_label": str(
            payload.get("source_language_label") or source_language
        ).strip(),
        "target_language_label": str(
            payload.get("target_language_label") or target_language
        ).strip(),
        "translated_content": translated_content,
        # Passive audit/debug metadata returned by the external service.
        # Frappe must not hard-code or decide model/provider behavior.
        "provider": provider,
        "model_name": model_name,
    }


class TranslationClient:
    """HTTP client for the external AOS Translation service."""

    def __init__(
        self,
        settings: TranslationClientSettings | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or get_translation_client_settings()
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.settings.service_url}/{path.lstrip('/')}"

    def _request_json(self, method: str, path: str) -> Dict[str, Any]:
        try:
            response = self.session.request(
                method=method.upper(),
                url=self._url(path),
                timeout=self.settings.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TranslationUnavailableError(
                "Translation service timed out. Please try again."
            ) from exc
        except requests.RequestException as exc:
            raise TranslationUnavailableError(
                "Translation service is unavailable. Please try again."
            ) from exc

        payload = _parse_json_response(response)

        if response.status_code >= 400:
            raise TranslationUnavailableError(_extract_error_message(payload))

        return payload

    def health_check(self) -> Dict[str, Any]:
        """Check the configured translation service health endpoint."""

        return self._request_json("GET", "/health")

    def ready_check(self) -> Dict[str, Any]:
        """Check the configured translation service readiness endpoint."""

        return self._request_json("GET", "/ready")

    def list_languages(self) -> Dict[str, Any]:
        """Return supported languages from the translation service."""

        return self._request_json("GET", "/languages")

    def translate_text(
        self,
        *,
        text: str,
        target_language: str,
        source_language: str | None = None,
    ) -> Dict[str, Any]:
        """Translate text using the configured internal translation service."""

        clean = _clean_text(text)
        target = _clean_language(target_language, fieldname="target_language")
        source = str(source_language or "").strip() or None

        if len(clean) > self.settings.max_characters:
            raise TranslationValidationError(
                f"Text cannot exceed {self.settings.max_characters} characters."
            )

        request_payload: Dict[str, Any] = {
            "text": clean,
            "target_language": target,
        }

        if source:
            request_payload["source_language"] = source

        try:
            response = self.session.post(
                self._url("/translate"),
                json=request_payload,
                timeout=self.settings.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise TranslationUnavailableError(
                "Translation service timed out. Please try again."
            ) from exc
        except requests.RequestException as exc:
            raise TranslationUnavailableError(
                "Translation service is unavailable. Please try again."
            ) from exc

        payload = _parse_json_response(response)

        if response.status_code == 400:
            raise TranslationValidationError(_extract_error_message(payload))

        if response.status_code >= 400:
            raise TranslationUnavailableError(_extract_error_message(payload))

        return _validate_translation_payload(payload)


def health_check() -> Dict[str, Any]:
    """Check the configured translation service health endpoint."""

    return TranslationClient().health_check()


def ready_check() -> Dict[str, Any]:
    """Check the configured translation service readiness endpoint."""

    return TranslationClient().ready_check()


def list_languages() -> Dict[str, Any]:
    """Return supported languages from the translation service."""

    return TranslationClient().list_languages()


def translate_text(
    *,
    text: str,
    target_language: str,
    source_language: str | None = None,
) -> Dict[str, Any]:
    """Translate text using the configured internal translation service."""

    return TranslationClient().translate_text(
        text=text,
        source_language=source_language,
        target_language=target_language,
    )
