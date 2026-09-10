"""Background removal service client.

Calls the internal AOS Background Removal Docker service.

Responsibilities:
- Read background-removal business limits from AOS Settings and service URL from env.
- Call the external FastAPI service over HTTP.
- Return processed PNG bytes to Frappe business logic.
- Convert connection/timeouts/service failures into clear application errors.

The actual AI/ML model, rembg, and ONNX Runtime do NOT run inside Frappe.
They run in the external background-removal service container.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, BinaryIO, Dict

import requests

from aos.utils.aos_config import (
    get_background_removal_service_secret,
    get_background_removal_service_url,
)
from aos.utils.aos_settings import get_aos_settings_snapshot


class BackgroundRemovalServiceError(Exception):
    """Base exception for background-removal integration failures."""


class BackgroundRemovalValidationError(BackgroundRemovalServiceError):
    """Raised when the request is invalid before calling the service."""


class BackgroundRemovalUnavailableError(BackgroundRemovalServiceError):
    """Raised when the service is unreachable or returns an invalid response."""


class BackgroundRemovalProcessingError(BackgroundRemovalServiceError):
    """Raised when the service rejects or fails to process the image."""


DEFAULT_SERVICE_URL = "http://127.0.0.1:8120"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class BackgroundRemovalClientSettings:
    service_url: str
    service_secret: str = field(repr=False)
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES


@dataclass(frozen=True)
class BackgroundRemovalResult:
    content: bytes
    content_type: str = "image/png"
    filename: str = "background-removed.png"


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


def get_background_removal_client_settings() -> BackgroundRemovalClientSettings:
    """Resolve integration settings from AOS Settings snapshot.

    Model/runtime settings intentionally do not appear here. They belong to the
    external background-removal service, not the Frappe business backend.
    """

    try:
        settings = get_aos_settings_snapshot()
    except Exception:
        # Defensive fallback for early install/migration contexts where the
        # singleton may not be available yet.
        return BackgroundRemovalClientSettings(
            service_url=DEFAULT_SERVICE_URL,
            service_secret=get_background_removal_service_secret(),
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            max_image_bytes=DEFAULT_MAX_IMAGE_BYTES,
        )

    return BackgroundRemovalClientSettings(
        service_url=_clean_url(
            get_background_removal_service_url(),
            default=DEFAULT_SERVICE_URL,
        ),
        service_secret=get_background_removal_service_secret(),
        timeout_seconds=_clamp_int(
            getattr(
                settings,
                "background_removal_service_timeout_seconds",
                DEFAULT_TIMEOUT_SECONDS,
            ),
            default=DEFAULT_TIMEOUT_SECONDS,
            min_value=1,
            max_value=180,
        ),
        max_image_bytes=_clamp_int(
            getattr(
                settings,
                "background_removal_max_image_bytes",
                DEFAULT_MAX_IMAGE_BYTES,
            ),
            default=DEFAULT_MAX_IMAGE_BYTES,
            min_value=1,
            max_value=50 * 1024 * 1024,
        ),
    )


def _parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        raise BackgroundRemovalUnavailableError(
            "Background removal service returned an invalid response."
        )

    if not isinstance(payload, dict):
        raise BackgroundRemovalUnavailableError(
            "Background removal service returned an invalid payload."
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

    return "Background removal service failed."



def _response_preview(response: requests.Response, *, limit: int = 500) -> str:
    """Return a short safe preview of a non-image upstream response."""

    try:
        text = response.text or ""
    except Exception:
        text = ""

    text = text.replace("\n", " ").replace("\r", " ").strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _is_png_response(response: requests.Response, content: bytes) -> bool:
    """Accept valid PNG responses even when proxies mangle Content-Type."""

    content_type = str(response.headers.get("content-type") or "").lower()
    output_format = str(response.headers.get("x-aos-output-format") or "").lower()

    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return True

    if "image/png" in content_type:
        return True

    if output_format == "png" and content:
        return True

    return False

def _file_tuple(
    image_file: Any,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> tuple[str, BinaryIO, str]:
    if image_file is None:
        raise BackgroundRemovalValidationError("Image file is required.")

    clean_filename = str(
        filename
        or getattr(image_file, "filename", None)
        or getattr(image_file, "name", None)
        or "image"
    ).strip()

    if not clean_filename:
        clean_filename = "image"

    clean_content_type = str(
        content_type
        or getattr(image_file, "content_type", None)
        or "application/octet-stream"
    ).strip()

    stream = getattr(image_file, "stream", None) or image_file

    if not hasattr(stream, "read"):
        raise BackgroundRemovalValidationError("Invalid image file.")

    try:
        stream.seek(0)
    except Exception:
        pass

    return clean_filename, stream, clean_content_type


class BackgroundRemovalClient:
    """HTTP client for the external AOS Background Removal service."""

    def __init__(
        self,
        settings: BackgroundRemovalClientSettings | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or get_background_removal_client_settings()
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
        except requests.RequestException as exc:
            raise BackgroundRemovalUnavailableError(
                "Background removal service is temporarily unavailable."
            ) from exc

        payload = _parse_json_response(response)

        if response.status_code >= 400:
            raise BackgroundRemovalUnavailableError(_extract_error_message(payload))

        return payload

    def health_check(self) -> Dict[str, Any]:
        return self._request_json("GET", "/health")

    def ready_check(self) -> Dict[str, Any]:
        return self._request_json("GET", "/ready")

    def remove_background(
        self,
        image_file: Any,
        *,
        filename: str | None = None,
        content_type: str | None = None,
    ) -> BackgroundRemovalResult:
        clean_filename, stream, clean_content_type = _file_tuple(
            image_file,
            filename=filename,
            content_type=content_type,
        )

        files = {
            "image": (
                clean_filename,
                stream,
                clean_content_type,
            )
        }

        try:
            response = self.session.post(
                self._url("/remove-background"),
                files=files,
                headers={"Authorization": f"Bearer {self.settings.service_secret}"},
                timeout=self.settings.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise BackgroundRemovalUnavailableError(
                "Background removal service is temporarily unavailable."
            ) from exc

        if response.status_code >= 400:
            _parse_json_response(response)

            if response.status_code in {400, 413, 415, 422}:
                raise BackgroundRemovalProcessingError(
                    "Background removal could not process this image."
                )

            raise BackgroundRemovalUnavailableError(
                "Background removal service is temporarily unavailable."
            )

        content = response.content or b""
        if not content:
            raise BackgroundRemovalUnavailableError(
                "Background removal service returned an empty image."
            )

        if not _is_png_response(response, content):
            raise BackgroundRemovalUnavailableError(
                "Background removal service returned an invalid image response."
            )

        return BackgroundRemovalResult(
            content=content,
            content_type="image/png",
            filename="background-removed.png",
        )


def health_check() -> Dict[str, Any]:
    return BackgroundRemovalClient().health_check()


def ready_check() -> Dict[str, Any]:
    return BackgroundRemovalClient().ready_check()


def remove_background_from_file(
    image_file: Any,
    *,
    filename: str | None = None,
    content_type: str | None = None,
) -> BackgroundRemovalResult:
    return BackgroundRemovalClient().remove_background(
        image_file,
        filename=filename,
        content_type=content_type,
    )
