"""MinIO-backed background removal API.

New flow:
- frontend uploads source image through aos.api.media.init_upload/confirm_upload
- frontend calls aos.api.media.remove_background with media_id
- backend downloads source bytes from MinIO
- backend calls the private background-removal service
- backend uploads result PNG to MinIO as a new AOS Media Object
"""

from __future__ import annotations

import io
import os
from uuid import uuid4

import frappe

try:
    from PIL import Image, UnidentifiedImageError
except Exception:  # pragma: no cover - defensive for minimal installs
    Image = None
    UnidentifiedImageError = Exception

from aos.api.shared.auth import require_authenticated_user
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.integrations.ai.background_removal_client import (
    BackgroundRemovalProcessingError,
    BackgroundRemovalUnavailableError,
    BackgroundRemovalValidationError,
    get_background_removal_client_settings,
    remove_background_from_file,
)
from aos.services.media.media_purposes import IMAGE_TYPES, get_media_purpose
from aos.services.media.media_service import (
    MediaNotFoundError,
    MediaPermissionError,
    MediaService,
    MediaValidationError,
    serialize_media_doc,
)

from .constants import REMOVE_BACKGROUND_LIMIT_PER_MINUTE_PER_USER


# Keep this conservative because background removal can be CPU/RAM heavy.
# 16MP example: 4000 x 4000.
MAX_REMOVE_BG_IMAGE_PIXELS = 16_000_000


class RemoveBackgroundValidationError(ValueError):
    """Raised when the source media is invalid for background removal."""


def remove_background_impl(**kwargs):
    """Remove background from a MinIO-backed AOS media image."""

    current_user, err = require_authenticated_user()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:media:remove_bg:user:{current_user}",
        ttl_seconds=60,
        limit=REMOVE_BACKGROUND_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    media_id = str(kwargs.get("media_id") or kwargs.get("id") or "").strip()
    if not media_id:
        return fail("Media id is required.", code="VALIDATION_ERROR")

    result_purpose = str(
        kwargs.get("result_purpose")
        or kwargs.get("output_purpose")
        or kwargs.get("purpose")
        or ""
    ).strip()

    try:
        service = MediaService()
        source = service.get_media_doc(media_id)
        service.assert_user_can_manage(source, current_user)

        _validate_source_media(source)

        if not result_purpose:
            result_purpose = source.purpose
        result_rule = get_media_purpose(result_purpose)
        if not result_rule:
            raise RemoveBackgroundValidationError("Invalid result media purpose.")
        if "image/png" not in result_rule.allowed_content_types:
            raise RemoveBackgroundValidationError(
                "Result purpose must support PNG images."
            )

        source_bytes = service.storage.get_bytes(source.bucket, source.object_key)
        _validate_source_size(source_bytes)
        width, height = _validate_image_body(source_bytes)

        source_filename = source.original_filename or "image.png"
        source_content_type = _clean_content_type(source.content_type)

        image_stream = io.BytesIO(source_bytes)
        image_stream.name = source_filename

        result = remove_background_from_file(
            image_stream,
            filename=source_filename,
            content_type=source_content_type,
        )

        result_filename = _build_output_filename(source_filename)
        result_width, result_height = _validate_result_image_body(result.content)

        result_doc = service.create_uploaded_from_bytes(
            user=current_user,
            purpose=result_rule.key,
            filename=result_filename,
            content_type="image/png",
            data=result.content,
            width=result_width or width,
            height=result_height or height,
        )

        url = service.get_url(media_id=result_doc.name, user=current_user)

        return ok(
            "Background removed successfully.",
            data={
                "media": serialize_media_doc(result_doc, url=url),
                "media_id": result_doc.name,
                "url": url,
                "source_media_id": source.name,
                "content_type": "image/png",
                "width": result_width or width,
                "height": result_height or height,
            },
        )

    except UnidentifiedImageError:
        return fail("Only valid image files are supported.", code="UNSUPPORTED_FILE_TYPE")
    except MediaNotFoundError as exc:
        return fail(str(exc), code="NOT_FOUND")
    except MediaPermissionError as exc:
        return fail(str(exc), code="FORBIDDEN")
    except (MediaValidationError, RemoveBackgroundValidationError) as exc:
        return fail(str(exc), code="VALIDATION_ERROR")
    except BackgroundRemovalValidationError as exc:
        return fail(str(exc), code="VALIDATION_ERROR")
    except BackgroundRemovalProcessingError as exc:
        return fail(str(exc), code="BACKGROUND_REMOVAL_FAILED", http_status=422)
    except BackgroundRemovalUnavailableError:
        return fail(
            "Background removal is temporarily unavailable. Please try again later.",
            code="BACKGROUND_REMOVAL_UNAVAILABLE",
            http_status=503,
        )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Media Remove Background Failed")
        return fail("Failed to remove background.", code="INTERNAL_ERROR")


def _validate_source_media(source) -> None:
    if source.status == "Deleted":
        raise MediaNotFoundError("Media not found")
    if source.status not in {"Uploaded", "Attached"}:
        raise RemoveBackgroundValidationError(
            "Media must be uploaded before background removal."
        )

    content_type = _clean_content_type(source.content_type)
    if content_type not in IMAGE_TYPES:
        raise RemoveBackgroundValidationError(
            "Only JPG, PNG, and WEBP images are supported."
        )


def _validate_source_size(source_bytes: bytes) -> None:
    settings = get_background_removal_client_settings()
    size_bytes = len(source_bytes or b"")

    if size_bytes <= 0:
        raise RemoveBackgroundValidationError("Image is empty.")

    if size_bytes > settings.max_image_bytes:
        raise RemoveBackgroundValidationError(
            f"Image exceeds maximum size of {settings.max_image_bytes} bytes."
        )


def _validate_image_body(source_bytes: bytes) -> tuple[int | None, int | None]:
    """Validate image bytes and return dimensions when Pillow is available."""

    if Image is None:
        # The external service still validates the actual image bytes.
        return None, None

    image = Image.open(io.BytesIO(source_bytes))
    image.load()

    width, height = image.size

    if width <= 0 or height <= 0:
        raise RemoveBackgroundValidationError("Invalid image dimensions.")

    if width * height > MAX_REMOVE_BG_IMAGE_PIXELS:
        raise RemoveBackgroundValidationError("Image is too large.")

    return int(width), int(height)


def _validate_result_image_body(result_bytes: bytes) -> tuple[int | None, int | None]:
    """Return result PNG dimensions when Pillow is available."""

    if Image is None:
        return None, None

    image = Image.open(io.BytesIO(result_bytes or b""))
    image.load()
    width, height = image.size
    return int(width), int(height)


def _clean_content_type(value: object) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _build_output_filename(original_filename: str) -> str:
    base_name = os.path.splitext(os.path.basename(original_filename or "image"))[0]
    base_name = _safe_filename_part(base_name) or "image"
    suffix = uuid4().hex[:8]
    return f"{base_name}_no_bg_{suffix}.png"


def _safe_filename_part(value: str) -> str:
    clean = str(value or "").strip().replace(" ", "-")
    clean = "".join(
        char for char in clean if char.isalnum() or char in {"-", "_", "."}
    )
    return clean.strip(".-_")[:80]
