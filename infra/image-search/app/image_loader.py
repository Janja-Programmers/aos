from __future__ import annotations

from io import BytesIO
from typing import BinaryIO
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, UnidentifiedImageError

from .config import Settings


class ImageLoadError(RuntimeError):
    pass


def _to_rgb_image(raw: bytes, *, source_label: str) -> Image.Image:
    try:
        image = Image.open(BytesIO(raw))
        image.load()
        return image.convert("RGB")
    except UnidentifiedImageError as exc:
        raise ImageLoadError(f"Unsupported or invalid image: {source_label}") from exc
    except Exception as exc:
        raise ImageLoadError(f"Failed to load image: {source_label}") from exc


def _read_limited(stream: BinaryIO, *, max_bytes: int, source_label: str) -> bytes:
    raw = stream.read(max_bytes + 1)

    if not raw:
        raise ImageLoadError(f"Image is empty: {source_label}")

    if len(raw) > max_bytes:
        raise ImageLoadError(
            f"Image exceeds maximum size of {max_bytes} bytes: {source_label}"
        )

    return raw


def _validate_allowed_url(url: str, settings: Settings) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ImageLoadError("Image URL must use http or https")
    if parsed.username or parsed.password:
        raise ImageLoadError("Image URL credentials are not allowed")

    host = parsed.hostname.lower().rstrip(".")
    allowed_hosts = {str(item).lower().rstrip(".") for item in settings.allowed_image_hosts}
    if not allowed_hosts or host not in allowed_hosts:
        raise ImageLoadError("Image URL host is not allowed")
    return url


def _resolve_url(image_url: str, settings: Settings) -> str:
    clean_url = str(image_url or "").strip()

    if not clean_url:
        raise ImageLoadError("image_url is required")

    if clean_url.startswith("http://") or clean_url.startswith("https://"):
        return _validate_allowed_url(clean_url, settings)

    if clean_url.startswith("/") and settings.file_base_url:
        resolved = urljoin(settings.file_base_url.rstrip("/") + "/", clean_url.lstrip("/"))
        return _validate_allowed_url(resolved, settings)

    raise ImageLoadError(
        "Image URL must be absolute. Configure IMAGE_SEARCH_FILE_BASE_URL only "
        "for local relative /files/... fallback."
    )


def load_image_from_url(image_url: str, *, settings: Settings) -> Image.Image:
    resolved_url = _resolve_url(image_url, settings)

    try:
        response = requests.get(
            resolved_url,
            timeout=settings.request_timeout_seconds,
            stream=True,
            allow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            raise ImageLoadError("Remote image redirects are not allowed")
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if content_type and "image/" not in content_type:
            raise ImageLoadError(f"URL did not return an image: {resolved_url}")

        raw = BytesIO()
        total = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > settings.max_image_bytes:
                raise ImageLoadError(
                    f"Remote image exceeds maximum size of {settings.max_image_bytes} bytes: {resolved_url}"
                )
            raw.write(chunk)

        return _to_rgb_image(raw.getvalue(), source_label=resolved_url)

    except ImageLoadError:
        raise
    except requests.RequestException as exc:
        raise ImageLoadError(f"Failed to fetch image URL: {resolved_url}") from exc


def load_image_from_file(
    image_file: BinaryIO,
    *,
    settings: Settings,
    source_label: str = "uploaded image",
) -> Image.Image:
    raw = _read_limited(
        image_file,
        max_bytes=settings.max_image_bytes,
        source_label=source_label,
    )
    return _to_rgb_image(raw, source_label=source_label)
