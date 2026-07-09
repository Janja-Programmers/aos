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
    ALLOWED_SOUND_EXTENSIONS,
    MAX_SOUND_FILE_SIZE_BYTES,
    MAX_SOUND_DURATION_SECONDS,
    DEFAULT_SOUND_SOURCE_TYPE,
    VALID_SOUND_SOURCE_TYPES,
)


# UPLOAD
def validate_filename(filename: str):
    if not filename:
        return None, fail("Filename is required", error="VALIDATION_ERROR")

    if "." not in filename:
        return None, fail("Invalid filename", error="VALIDATION_ERROR")

    ext = filename.split(".")[-1].lower()

    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        return None, fail("Unsupported file type", error="VALIDATION_ERROR")

    return ext, None


def validate_file_size(size_bytes: int | str | None):
    if size_bytes is None:
        return None

    try:
        size_bytes = int(size_bytes)
    except Exception:
        return fail("Invalid file size", error="VALIDATION_ERROR")

    if size_bytes > MAX_VIDEO_FILE_SIZE_BYTES:
        return fail("File too large", error="VALIDATION_ERROR")
    return None


# SHORT METADATA
def validate_content_mode(content_mode: str | None):
    if not content_mode:
        return DEFAULT_SHORT_CONTENT_MODE, None

    content_mode = str(content_mode).strip().lower()

    if content_mode not in VALID_SHORT_CONTENT_MODES:
        return None, fail(
            "Invalid content mode",
            error="VALIDATION_ERROR",
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
            error="VALIDATION_ERROR",
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
            error="VALIDATION_ERROR",
        )

    return None


# COMMENTS
def validate_comment_text(text: str):
    if not text:
        return None, fail("Comment cannot be empty", error="VALIDATION_ERROR")

    text = text.strip()

    if not text:
        return None, fail("Comment cannot be empty", error="VALIDATION_ERROR")

    if len(text) > COMMENT_MAX_LENGTH:
        return None, fail(
            "Comment too long",
            error="VALIDATION_ERROR",
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


# SOUNDS
def validate_sound_filename(filename: str):
    if not filename:
        return None, fail("Filename is required", error="VALIDATION_ERROR")

    if "." not in filename:
        return None, fail("Invalid filename", error="VALIDATION_ERROR")

    ext = filename.split(".")[-1].lower()

    if ext not in ALLOWED_SOUND_EXTENSIONS:
        return None, fail("Unsupported audio file type", error="VALIDATION_ERROR")

    return ext, None


def validate_sound_file_size(size_bytes: int | str | None):
    if size_bytes is None:
        return None

    try:
        size_bytes = int(size_bytes)
    except Exception:
        return fail("Invalid file size", error="VALIDATION_ERROR")

    if size_bytes > MAX_SOUND_FILE_SIZE_BYTES:
        return fail("Sound file too large", error="VALIDATION_ERROR")

    return None


def validate_sound_title(title: str | None):
    if not title:
        return None, fail("Sound title is required", error="VALIDATION_ERROR")

    title = str(title).strip()
    if not title:
        return None, fail("Sound title is required", error="VALIDATION_ERROR")

    if len(title) > 140:
        return None, fail("Sound title is too long", error="VALIDATION_ERROR")

    return title, None


def validate_sound_artist(artist: str | None):
    if not artist:
        return "", None

    artist = str(artist).strip()
    if len(artist) > 140:
        return None, fail("Sound artist is too long", error="VALIDATION_ERROR")

    return artist, None


def validate_sound_source_type(source_type: str | None):
    if not source_type:
        return DEFAULT_SOUND_SOURCE_TYPE, None

    source_type = str(source_type).strip().lower()
    if source_type not in VALID_SOUND_SOURCE_TYPES:
        return None, fail("Invalid sound source type", error="VALIDATION_ERROR")

    return source_type, None


def validate_sound_duration(duration_seconds):
    if duration_seconds in (None, ""):
        return 0, None

    try:
        duration_seconds = float(duration_seconds)
    except Exception:
        return None, fail("Invalid sound duration", error="VALIDATION_ERROR")

    if duration_seconds < 0:
        return None, fail("Invalid sound duration", error="VALIDATION_ERROR")

    if duration_seconds > MAX_SOUND_DURATION_SECONDS:
        return None, fail(
            f"Sound must be <= {MAX_SOUND_DURATION_SECONDS} seconds",
            error="VALIDATION_ERROR",
        )

    return duration_seconds, None


def validate_sound_timing(start_ms=None, duration_ms=None, volume=None):
    try:
        start_ms = int(start_ms or 0)
    except Exception:
        return None, None, None, fail("Invalid sound_start_ms", error="VALIDATION_ERROR")

    try:
        duration_ms = int(duration_ms or 0)
    except Exception:
        return None, None, None, fail("Invalid sound_duration_ms", error="VALIDATION_ERROR")

    try:
        volume = float(volume if volume is not None else 1.0)
    except Exception:
        return None, None, None, fail("Invalid sound_volume", error="VALIDATION_ERROR")

    if start_ms < 0:
        return None, None, None, fail("sound_start_ms cannot be negative", error="VALIDATION_ERROR")

    if duration_ms < 0:
        return None, None, None, fail("sound_duration_ms cannot be negative", error="VALIDATION_ERROR")

    if volume < 0 or volume > 1:
        return None, None, None, fail("sound_volume must be between 0 and 1", error="VALIDATION_ERROR")

    return start_ms, duration_ms, volume, None
