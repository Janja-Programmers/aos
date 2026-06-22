from __future__ import annotations

from io import BytesIO
from typing import BinaryIO

from PIL import Image, UnidentifiedImageError

from .config import Settings


class ImageLoadError(RuntimeError):
    pass


def _read_limited(stream: BinaryIO, *, max_bytes: int, source_label: str) -> bytes:
    raw = stream.read(max_bytes + 1)

    if not raw:
        raise ImageLoadError(f"Image is empty: {source_label}")

    if len(raw) > max_bytes:
        raise ImageLoadError(
            f"Image exceeds maximum size of {max_bytes} bytes: {source_label}"
        )

    return raw


def load_image_bytes(
    image_file: BinaryIO,
    *,
    settings: Settings,
    source_label: str = "uploaded image",
) -> bytes:
    """Read uploaded image bytes with a hard size cap."""
    return _read_limited(
        image_file,
        max_bytes=settings.max_image_bytes,
        source_label=source_label,
    )


def validate_image_bytes(raw: bytes, *, source_label: str) -> None:
    """Validate that bytes represent an image Pillow can open."""
    try:
        image = Image.open(BytesIO(raw))
        image.verify()
    except UnidentifiedImageError as exc:
        raise ImageLoadError(f"Unsupported or invalid image: {source_label}") from exc
    except Exception as exc:
        raise ImageLoadError(f"Failed to validate image: {source_label}") from exc
