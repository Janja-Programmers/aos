from __future__ import annotations

import io
import os
from urllib.parse import unquote, urlparse

import frappe
from PIL import Image, UnidentifiedImageError
from rembg import remove

from aos.api.shared.auth import require_login
from aos.api.shared.rate_limit import rate_limit
from aos.api.shared.responses import fail, ok

from .constants import REMOVE_BG_LIMIT_PER_MINUTE_PER_USER


# Keep this conservative because rembg can be CPU/RAM heavy.
# 16MP example: 4000 x 4000.
MAX_REMOVE_BG_IMAGE_PIXELS = 16_000_000

SUPPORTED_IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def remove_background_impl(**kwargs):
    """
    Remove background from an uploaded image.

    Rules:
    - Requires logged-in user.
    - User can only process files they own.
    - Only supports image files.
    - Preserves original canvas size.
    - Does NOT crop.
    - Does NOT resize.
    - Does NOT reposition the subject.
    - Returns a new PNG file with transparent background.
    """
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

        if not os.path.exists(file_path):
            return fail("File not found on disk.", code="FILE_MISSING")

        _validate_supported_extension(file_doc.file_name or resolved_filename)

        input_image = _open_image(file_path)

        _validate_image_size(input_image)

        original_width, original_height = input_image.size

        # Convert to RGBA so the output can preserve transparency.
        input_image = input_image.convert("RGBA")

        # IMPORTANT:
        # remove background only.
        # Do not crop, resize, or reposition.
        output_image = remove(input_image)

        output_image = _ensure_pil_image(output_image)

        # Defensive check:
        # rembg should preserve size, but this guarantees the API contract.
        if output_image.size != (original_width, original_height):
            output_image = output_image.resize(
                (original_width, original_height),
                Image.Resampling.LANCZOS,
            )

        buffer = io.BytesIO()
        output_image.save(buffer, format="PNG")
        buffer.seek(0)

        new_filename = _build_output_filename(file_doc.file_name or resolved_filename)

        new_file = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": new_filename,
                "content": buffer.getvalue(),
                "is_private": file_doc.is_private,
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
                "width": original_width,
                "height": original_height,
            },
        )

    except UnidentifiedImageError:
        return fail("Only valid image files are supported.", code="UNSUPPORTED_FILE_TYPE")

    except ValueError as e:
        return fail(str(e), code="VALIDATION_ERROR")

    except Exception:
        frappe.log_error(frappe.get_traceback(), "AOS Remove Background Failed")
        return fail("Failed to remove background.", code="INTERNAL_ERROR")


def _resolve_file_path(file_doc) -> tuple[str, str]:
    """
    Resolve the actual site file path from a Frappe File document.

    Supports standard Frappe URLs:
    - /files/example.png
    - /private/files/example.png

    Returns:
    - absolute file path
    - resolved filename
    """

    parsed_path = urlparse(file_doc.file_url).path
    filename = unquote(os.path.basename(parsed_path))

    if not filename:
        raise ValueError("Invalid file path.")

    if file_doc.is_private:
        file_path = frappe.get_site_path("private", "files", filename)
    else:
        file_path = frappe.get_site_path("public", "files", filename)

    return file_path, filename


def _validate_supported_extension(filename: str) -> None:
    """
    Validate image extension before opening/processing.

    This is not the only validation. PIL still validates the actual file body.
    """

    _, ext = os.path.splitext(filename or "")
    ext = ext.lower().strip()

    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise ValueError("Only JPG, PNG, and WEBP images are supported.")


def _open_image(file_path: str) -> Image.Image:
    """
    Open image safely using Pillow.
    """

    image = Image.open(file_path)

    # Force image loading now so corrupt files fail here,
    # not later during rembg processing.
    image.load()

    return image


def _validate_image_size(image: Image.Image) -> None:
    """
    Prevent very large images from overwhelming rembg processing.
    """

    width, height = image.size

    if width <= 0 or height <= 0:
        raise ValueError("Invalid image dimensions.")

    if width * height > MAX_REMOVE_BG_IMAGE_PIXELS:
        raise ValueError("Image is too large.")


def _ensure_pil_image(output) -> Image.Image:
    """
    rembg can return a PIL image when given a PIL image,
    but this keeps the implementation defensive.
    """

    if isinstance(output, Image.Image):
        return output.convert("RGBA")

    if isinstance(output, bytes):
        image = Image.open(io.BytesIO(output))
        image.load()
        return image.convert("RGBA")

    raise ValueError("Failed to process image output.")


def _build_output_filename(original_filename: str) -> str:
    """
    Build deterministic PNG output filename.
    """

    base_name = os.path.splitext(os.path.basename(original_filename or "image"))[0]
    base_name = base_name.strip() or "image"

    return f"{base_name}_no_bg.png"
