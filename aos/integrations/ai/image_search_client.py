"""Image search service client.

Calls the internal AOS Image Search Docker service.

Responsibilities:
- Read image-search business limits from AOS Settings and service URL from env.
- Call the external FastAPI service over HTTP.
- Normalize and validate response shapes.
- Convert connection/timeouts/service failures into clear application errors.

The actual AI/ML model and Qdrant vector logic do NOT run inside Frappe.
They run in the external image-search service container.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO, Dict, Iterable, List
from urllib.parse import quote

import requests

from aos.utils.aos_config import get_image_search_service_url
from aos.utils.aos_settings import get_aos_settings_snapshot


class ImageSearchServiceError(Exception):
    """Base exception for image-search integration failures."""


class ImageSearchValidationError(ImageSearchServiceError):
    """Raised when the image-search request is invalid before calling the service."""


class ImageSearchUnavailableError(ImageSearchServiceError):
    """Raised when the image-search service is unreachable or returns an invalid response."""


DEFAULT_SERVICE_URL = "http://127.0.0.1:8110"
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_LIMIT = 20
MAX_LIMIT = 100


@dataclass(frozen=True)
class ImageSearchClientSettings:
    service_url: str
    timeout_seconds: int
    default_limit: int
    max_limit: int


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


def get_image_search_client_settings() -> ImageSearchClientSettings:
    """Resolve image-search integration settings from the AOS Settings snapshot.

    Qdrant/model/vector settings intentionally do not appear here. They belong
    to the external image-search service, not the Frappe business backend.
    """

    try:
        settings = get_aos_settings_snapshot()
    except Exception:
        # Defensive fallback for early install/migration contexts where the
        # singleton may not be available yet.
        return ImageSearchClientSettings(
            service_url=DEFAULT_SERVICE_URL,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            default_limit=DEFAULT_LIMIT,
            max_limit=MAX_LIMIT,
        )

    service_url = _clean_url(
        get_image_search_service_url(),
        default=DEFAULT_SERVICE_URL,
    )

    timeout_seconds = _clamp_int(
        getattr(settings, "image_search_service_timeout_seconds", DEFAULT_TIMEOUT_SECONDS),
        default=DEFAULT_TIMEOUT_SECONDS,
        min_value=1,
        max_value=120,
    )

    max_limit = _clamp_int(
        getattr(settings, "image_search_max_limit", MAX_LIMIT),
        default=MAX_LIMIT,
        min_value=1,
        max_value=MAX_LIMIT,
    )

    default_limit = _clamp_int(
        getattr(settings, "image_search_default_limit", DEFAULT_LIMIT),
        default=DEFAULT_LIMIT,
        min_value=1,
        max_value=max_limit,
    )

    return ImageSearchClientSettings(
        service_url=service_url,
        timeout_seconds=timeout_seconds,
        default_limit=default_limit,
        max_limit=max_limit,
    )


def _parse_json_response(response: requests.Response) -> Dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        raise ImageSearchUnavailableError(
            "Image search service returned an invalid response."
        )

    if not isinstance(payload, dict):
        raise ImageSearchUnavailableError(
            "Image search service returned an invalid payload."
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

    return "Image search service failed."


def _clean_ad_id(ad_id: str | None) -> str:
    clean = str(ad_id or "").strip()
    if not clean:
        raise ImageSearchValidationError("ad_id is required.")
    return clean


def _normalize_limit(value: Any, *, settings: ImageSearchClientSettings) -> int:
    return _clamp_int(
        value,
        default=settings.default_limit,
        min_value=1,
        max_value=settings.max_limit,
    )


def _clean_image_references(images: Iterable[Dict[str, Any]] | None) -> List[Dict[str, Any]]:
    clean: List[Dict[str, Any]] = []

    for row in images or []:
        if not isinstance(row, dict):
            continue

        image_url = str(
            row.get("image_url")
            or row.get("image")
            or row.get("file_url")
            or ""
        ).strip()

        if not image_url:
            continue

        clean.append(
            {
                "image_url": image_url,
                "is_primary": bool(int(row.get("is_primary") or 0)),
                "sort_order": _clamp_int(
                    row.get("sort_order"),
                    default=0,
                    min_value=0,
                    max_value=999,
                ),
            }
        )

    return clean


def _file_tuple(image_file: Any) -> tuple[str, BinaryIO, str]:
    if image_file is None:
        raise ImageSearchValidationError("Image file is required.")

    filename = str(
        getattr(image_file, "filename", None)
        or getattr(image_file, "name", None)
        or "image"
    ).strip()

    if not filename:
        filename = "image"

    content_type = str(
        getattr(image_file, "content_type", None)
        or "application/octet-stream"
    ).strip()

    stream = getattr(image_file, "stream", None) or image_file

    if not hasattr(stream, "read"):
        raise ImageSearchValidationError("Invalid image file.")

    try:
        stream.seek(0)
    except Exception:
        pass

    return filename, stream, content_type


class ImageSearchClient:
    """HTTP client for the external AOS Image Search service."""

    def __init__(
        self,
        settings: ImageSearchClientSettings | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.settings = settings or get_image_search_client_settings()
        self.session = session or requests.Session()

    def _url(self, path: str) -> str:
        return f"{self.settings.service_url}/{path.lstrip('/')}"

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        try:
            response = self.session.request(
                method=method.upper(),
                url=self._url(path),
                json=json,
                timeout=self.settings.timeout_seconds,
            )
        except requests.Timeout:
            raise ImageSearchUnavailableError(
                "Image search service timed out. Please try again."
            )
        except requests.RequestException:
            raise ImageSearchUnavailableError(
                "Image search service is unavailable. Please try again."
            )

        payload = _parse_json_response(response)

        if response.status_code >= 400:
            raise ImageSearchUnavailableError(_extract_error_message(payload))

        return payload

    def health_check(self) -> Dict[str, Any]:
        """Call GET /health on the image-search service."""

        try:
            response = self.session.get(
                self._url("/health"),
                timeout=self.settings.timeout_seconds,
            )
        except requests.Timeout:
            raise ImageSearchUnavailableError(
                "Image search service health check timed out."
            )
        except requests.RequestException:
            raise ImageSearchUnavailableError("Image search service is unavailable.")

        payload = _parse_json_response(response)

        if response.status_code >= 400:
            raise ImageSearchUnavailableError(_extract_error_message(payload))

        return payload

    def ready_check(self) -> Dict[str, Any]:
        """Call GET /ready on the image-search service."""

        try:
            response = self.session.get(
                self._url("/ready"),
                timeout=self.settings.timeout_seconds,
            )
        except requests.Timeout:
            raise ImageSearchUnavailableError(
                "Image search service readiness check timed out."
            )
        except requests.RequestException:
            raise ImageSearchUnavailableError("Image search service is unavailable.")

        payload = _parse_json_response(response)

        if response.status_code >= 400:
            raise ImageSearchUnavailableError(_extract_error_message(payload))

        return payload

    def replace_ad_images(
        self,
        *,
        ad_id: str,
        images: Iterable[Dict[str, Any]] | None,
    ) -> Dict[str, Any]:
        """Replace all indexed image vectors for an ad.

        The operation is intentionally idempotent. The AI service deletes the
        ad's current vectors and indexes the supplied current image set.
        """

        clean_ad_id = _clean_ad_id(ad_id)
        payload = {
            "images": _clean_image_references(images),
        }

        result = self._request_json(
            "POST",
            f"/ads/{quote(clean_ad_id, safe='')}/replace-images",
            json=payload,
        )

        if result.get("ok") is not True:
            raise ImageSearchUnavailableError(_extract_error_message(result))

        return {
            "ok": True,
            "ad_id": str(result.get("ad_id") or clean_ad_id),
            "indexed_count": _clamp_int(
                result.get("indexed_count"),
                default=0,
                min_value=0,
                max_value=1000,
            ),
            "failed_count": _clamp_int(
                result.get("failed_count"),
                default=0,
                min_value=0,
                max_value=1000,
            ),
            "message": result.get("message"),
        }

    def delete_ad_vectors(self, *, ad_id: str) -> Dict[str, Any]:
        """Delete all indexed image vectors for an ad."""

        clean_ad_id = _clean_ad_id(ad_id)
        result = self._request_json(
            "DELETE",
            f"/ads/{quote(clean_ad_id, safe='')}/vectors",
        )

        if result.get("ok") is not True:
            raise ImageSearchUnavailableError(_extract_error_message(result))

        return {
            "ok": True,
            "ad_id": str(result.get("ad_id") or clean_ad_id),
            "deleted": bool(result.get("deleted", True)),
            "message": result.get("message"),
        }

    def search_by_image_file(
        self,
        *,
        image_file: Any,
        limit: Any = None,
    ) -> Dict[str, Any]:
        """Search visually similar ads using an uploaded image file."""

        search_limit = _normalize_limit(limit, settings=self.settings)
        filename, stream, content_type = _file_tuple(image_file)

        try:
            response = self.session.post(
                self._url("/search/image"),
                files={"image": (filename, stream, content_type)},
                data={"limit": str(search_limit)},
                timeout=self.settings.timeout_seconds,
            )
        except requests.Timeout:
            raise ImageSearchUnavailableError(
                "Image search service timed out. Please try again."
            )
        except requests.RequestException:
            raise ImageSearchUnavailableError(
                "Image search service is unavailable. Please try again."
            )

        payload = _parse_json_response(response)

        if response.status_code >= 400:
            raise ImageSearchUnavailableError(_extract_error_message(payload))

        if payload.get("ok") is not True:
            raise ImageSearchUnavailableError(_extract_error_message(payload))

        return {
            "ok": True,
            "items": _clean_search_items(payload.get("items")),
            "message": payload.get("message") or "Search successful.",
        }


def _clean_search_items(items: Any) -> List[Dict[str, Any]]:
    if not isinstance(items, list):
        raise ImageSearchUnavailableError(
            "Image search service returned invalid search results."
        )

    clean: List[Dict[str, Any]] = []

    for row in items:
        if not isinstance(row, dict):
            continue

        ad_id = str(row.get("ad_id") or "").strip()
        if not ad_id:
            continue

        try:
            score = float(row.get("score") or 0)
        except Exception:
            score = 0.0

        clean.append(
            {
                "ad_id": ad_id,
                "score": score,
                "matched_image_url": str(row.get("matched_image_url") or "").strip() or None,
                "is_primary": bool(row.get("is_primary") or False),
            }
        )

    return clean


# Convenience wrappers for business/API modules.
def replace_ad_images(
    *,
    ad_id: str,
    images: Iterable[Dict[str, Any]] | None,
) -> Dict[str, Any]:
    return ImageSearchClient().replace_ad_images(ad_id=ad_id, images=images)


def delete_ad_vectors(*, ad_id: str) -> Dict[str, Any]:
    return ImageSearchClient().delete_ad_vectors(ad_id=ad_id)


def search_by_image_file(*, image_file: Any, limit: Any = None) -> Dict[str, Any]:
    return ImageSearchClient().search_by_image_file(
        image_file=image_file,
        limit=limit,
    )


def health_check() -> Dict[str, Any]:
    return ImageSearchClient().health_check()


def ready_check() -> Dict[str, Any]:
    return ImageSearchClient().ready_check()
