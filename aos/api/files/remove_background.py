"""
Background removal API.

This endpoint keeps business/file logic in Frappe and delegates AI/ML image
processing to the external background-removal service.

Rules:
- Login is required.
- User can only process files they own.
- Original file is never modified.
- Processed output is saved as a new PNG File.
- Frappe does not import or run rembg / ONNX Runtime.
"""

from __future__ import annotations

import io
import mimetypes
import os
from urllib.parse import unquote, urlparse
from uuid import uuid4

import frappe
try:
    from PIL import Image, UnidentifiedImageError
except Exception:  # pragma: no cover - defensive for minimal installs
    Image = None
    UnidentifiedImageError = Exception

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok
from aos.integrations.ai.background_removal_client import (
    BackgroundRemovalProcessingError,
    BackgroundRemovalUnavailableError,
    BackgroundRemovalValidationError,
    get_background_removal_client_settings,
    remove_background_from_file,
)

from .constants import REMOVE_BG_LIMIT_PER_MINUTE_PER_USER


SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}

SUPPORTED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}

# Keep this conservative because background removal can be CPU/RAM heavy.
# 16MP example: 4000 x 4000.
MAX_REMOVE_BG_IMAGE_PIXELS = 16_000_000


class RemoveBackgroundValidationError(ValueError):
    """Raised when the source file is invalid for background removal."""


def remove_background_impl(**kwargs):
    """Remove background from an uploaded image using the external AI service."""

    current_user, err = require_login()
    if err:
        return err

    rl = rate_limit(
        key=f"aos:files:remove_bg:user:{current_user}",
        ttl_seconds=60,
        limit=REMOVE_BG_LIMIT_PER_MINUTE_PER_USER,
        message="Too many requests. Please try again shortly.",
    )
    if rl:
        return rl

    file_id = kwargs.get("file_id")

    if not file_id:
        return fail("File id is required.", code="VALIDATION_ERROR")

    file_doc = frappe.db.get_value(
        "File",
        file_id,
        ["name", "owner", "file_url", "file_name", "is_private"],
        as_dict=True,
    )

    if not file_doc:
        return fail("File not found.", code="NOT_FOUND")

    if file_doc.owner != current_user:
        return fail("You cannot edit this file.", code="PERMISSION_DENIED")

    if not file_doc.file_url:
        return fail("File URL is missing.", code="INVALID_FILE")

    try:
        file_path, resolved_filename = _resolve_file_path(file_doc)
        source_filename = file_doc.file_name or resolved_filename

        if not os.path.exists(file_path):
            return fail("File not found on disk.", code="FILE_MISSING")

        _validate_supported_extension(source_filename)
        _validate_file_size(file_path)

        width, height = _validate_image_body(file_path)

        content_type = _guess_content_type(source_filename)

        with open(file_path, "rb") as image_file:
            result = remove_background_from_file(
                image_file,
                filename=source_filename,
                content_type=content_type,
            )

        new_filename = _build_output_filename(source_filename)

        new_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": new_filename,
                "content": result.content,
                "is_private": int(file_doc.is_private or 0),
                "owner": current_user,
            }
        )
        new_file.insert(ignore_permissions=True)

        return ok(
            "Background removed successfully.",
            data={
                "file_id": new_file.name,
                "file_url": new_file.file_url,
                "file_name": new_file.file_name,
                "is_private": new_file.is_private,
                "content_type": result.content_type,
                "width": width,
                "height": height,
            },
        )

    except UnidentifiedImageError:
        return fail("Only valid image files are supported.", code="UNSUPPORTED_FILE_TYPE")

    except RemoveBackgroundValidationError as exc:
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
        frappe.log_error(frappe.get_traceback(), "AOS Remove Background Failed")
        return fail("Failed to remove background.", code="INTERNAL_ERROR")


def _resolve_file_path(file_doc) -> tuple[str, str]:
    """Resolve a standard Frappe File URL into a site-local file path."""

    parsed_path = urlparse(file_doc.file_url).path
    filename = unquote(os.path.basename(parsed_path))

    if not filename:
        raise RemoveBackgroundValidationError("Invalid file path.")

    if int(file_doc.is_private or 0):
        file_path = frappe.get_site_path("private", "files", filename)
    else:
        file_path = frappe.get_site_path("public", "files", filename)

    return file_path, filename


def _validate_supported_extension(filename: str) -> None:
    _, ext = os.path.splitext(filename or "")
    ext = ext.lower().strip()

    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise RemoveBackgroundValidationError(
            "Only JPG, PNG, and WEBP images are supported."
        )


def _validate_file_size(file_path: str) -> None:
    settings = get_background_removal_client_settings()
    size_bytes = os.path.getsize(file_path)

    if size_bytes <= 0:
        raise RemoveBackgroundValidationError("Image is empty.")

    if size_bytes > settings.max_image_bytes:
        raise RemoveBackgroundValidationError(
            f"Image exceeds maximum size of {settings.max_image_bytes} bytes."
        )


def _validate_image_body(file_path: str) -> tuple[int | None, int | None]:
    """Validate the image body and return dimensions when Pillow is available."""

    if Image is None:
        # The external service still validates the actual image bytes.
        return None, None

    image = Image.open(file_path)
    image.load()

    width, height = image.size

    if width <= 0 or height <= 0:
        raise RemoveBackgroundValidationError("Invalid image dimensions.")

    if width * height > MAX_REMOVE_BG_IMAGE_PIXELS:
        raise RemoveBackgroundValidationError("Image is too large.")

    return int(width), int(height)


def _guess_content_type(filename: str) -> str:
    content_type, _ = mimetypes.guess_type(filename or "")
    content_type = (content_type or "application/octet-stream").strip().lower()

    if content_type not in SUPPORTED_CONTENT_TYPES:
        # Extension validation already passed. Use octet-stream fallback only if
        # Python's mimetype table is incomplete for the environment.
        _, ext = os.path.splitext(filename or "")
        ext = ext.lower().strip()
        if ext in {".jpg", ".jpeg"}:
            return "image/jpeg"
        if ext == ".png":
            return "image/png"
        if ext == ".webp":
            return "image/webp"

    return content_type


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
