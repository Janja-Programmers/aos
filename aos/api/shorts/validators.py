"""
Validators for Shorts API.

Responsible for:
- Input validation
- Basic normalization
- Preventing bad requests early

Do NOT include business logic here.
"""

from __future__ import annotations

from aos.api.shared.responses import fail

from aos.api.shorts.constants import (
    MAX_SHORT_DURATION_SECONDS,
    CAPTION_MAX_LENGTH,
    MAX_HASHTAGS,
    COMMENT_MAX_LENGTH,
    ALLOWED_VIDEO_EXTENSIONS,
    MAX_VIDEO_FILE_SIZE_BYTES,
    DEFAULT_SHORT_CONTENT_MODE,
    VALID_SHORT_CONTENT_MODES,
)


# UPLOAD
def validate_filename(filename: str):
    if not filename:
        return None, fail("Filename is required", code="VALIDATION_ERROR")

    if "." not in filename:
        return None, fail("Invalid filename", code="VALIDATION_ERROR")

    ext = filename.split(".")[-1].lower()

    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return None, fail("Unsupported file type", code="VALIDATION_ERROR")

    return ext, None


def validate_file_size(size_bytes: int | str | None):
    if size_bytes is None:
        return None

    try:
        size_bytes = int(size_bytes)
    except Exception:
        return fail("Invalid file size", code="VALIDATION_ERROR")

    if size_bytes > MAX_VIDEO_FILE_SIZE_BYTES:
        return fail("File too large", code="VALIDATION_ERROR")
    return None


# SHORT METADATA
def validate_content_mode(content_mode: str | None):
    if not content_mode:
        return DEFAULT_SHORT_CONTENT_MODE, None

    content_mode = str(content_mode).strip().lower()

    if content_mode not in VALID_SHORT_CONTENT_MODES:
        return None, fail(
            "Invalid content mode",
            code="VALIDATION_ERROR",
            data={"field": "content_mode"},
        )

    return content_mode, None


def validate_caption(caption: str | None):
    if not caption:
        return "", None

    caption = caption.strip()

    if len(caption) > CAPTION_MAX_LENGTH:
        return None, fail(
            "Caption too long",
            code="VALIDATION_ERROR",
            data={"field": "caption"},
        )

    return caption, None


def normalize_hashtags(hashtags):
    if not hashtags:
        return []

    if isinstance(hashtags, str):
        import json
        try:
            hashtags = json.loads(hashtags)
        except Exception:
            return []

    if not isinstance(hashtags, list):
        return []

    clean = []
    seen = set()

    for tag in hashtags:
        if not tag:
            continue

        tag = str(tag).strip().lower()

        if tag.startswith("#"):
            tag = tag[1:]

        if not tag or tag in seen:
            continue

        seen.add(tag)
        clean.append(tag)

        if len(clean) >= MAX_HASHTAGS:
            break

    return clean


def validate_duration(duration_seconds: float | None):
    if not duration_seconds:
        return None

    if duration_seconds > MAX_SHORT_DURATION_SECONDS:
        return fail(
            f"Short must be <= {MAX_SHORT_DURATION_SECONDS} seconds",
            code="VALIDATION_ERROR",
        )

    return None


# COMMENTS
def validate_comment_text(text: str):
    if not text:
        return None, fail("Comment cannot be empty", code="VALIDATION_ERROR")

    text = text.strip()

    if not text:
        return None, fail("Comment cannot be empty", code="VALIDATION_ERROR")

    if len(text) > COMMENT_MAX_LENGTH:
        return None, fail(
            "Comment too long",
            code="VALIDATION_ERROR",
            data={"field": "text"},
        )

    return text, None


# PAGINATION
def validate_limit(value, default: int, max_limit: int):
    """
    Normalize and clamp limit value.

    - Converts to int
    - Applies default if invalid
    - Enforces max limit
    """
    try:
        value = int(value)
    except Exception:
        return default

    if value <= 0:
        return default

    return min(value, max_limit)
