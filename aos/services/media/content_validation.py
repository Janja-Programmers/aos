"""Defensive media filename, extension, signature, and metadata validation."""

from __future__ import annotations

import hashlib
import io
import os
import re
import unicodedata
import warnings
from collections.abc import Callable, Iterable
from pathlib import PurePath

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except Exception:  # pragma: no cover - Frappe runtime installs Pillow through dependencies
    Image = None
    ImageOps = None

    class UnidentifiedImageError(Exception):
        pass


MAX_DISPLAY_FILENAME_LENGTH = 140
MAX_IMAGE_PIXELS = 80_000_000
SNIFF_BYTES = 64 * 1024
CHUNK_SIZE = 1024 * 1024

_DANGEROUS_EXTENSIONS = {
    ".apk",
    ".app",
    ".bat",
    ".bin",
    ".cgi",
    ".cmd",
    ".com",
    ".dll",
    ".dmg",
    ".exe",
    ".hta",
    ".htm",
    ".html",
    ".jar",
    ".js",
    ".jsp",
    ".lnk",
    ".msi",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".rb",
    ".scr",
    ".sh",
    ".svg",
    ".vbs",
    ".wasm",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_SAFE_FILENAME_RE = re.compile(r"[^\w.()\- ]+", re.UNICODE)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MediaContentValidationError(ValueError):
    """Raised for public-safe content validation failures."""

    def __init__(self, message: str, *, code: str = "INVALID_FILE"):
        super().__init__(message)
        self.code = code


def normalize_filename(filename: object) -> str:
    raw = unicodedata.normalize("NFKC", str(filename or "").strip())
    if not raw:
        raise MediaContentValidationError("Filename is required.", code="INVALID_FILENAME")
    if _CONTROL_RE.search(raw) or "\x00" in raw:
        raise MediaContentValidationError("Filename contains invalid characters.", code="INVALID_FILENAME")
    if raw.startswith("."):
        raise MediaContentValidationError("Hidden filenames are not allowed.", code="INVALID_FILENAME")
    if "/" in raw or "\\" in raw or raw in {".", ".."}:
        raise MediaContentValidationError("Filename must not contain a path.", code="INVALID_FILENAME")

    clean = _SAFE_FILENAME_RE.sub("_", raw).strip(" .")
    clean = re.sub(r"\s+", " ", clean)
    if not clean or clean in {".", ".."}:
        raise MediaContentValidationError("Filename is invalid.", code="INVALID_FILENAME")
    if clean.startswith("."):
        raise MediaContentValidationError("Hidden filenames are not allowed.", code="INVALID_FILENAME")

    suffixes = [suffix.lower() for suffix in PurePath(clean).suffixes]
    if any(suffix in _DANGEROUS_EXTENSIONS for suffix in suffixes[:-1]):
        raise MediaContentValidationError("Filename uses an unsafe double extension.", code="INVALID_FILENAME")
    if suffixes and suffixes[-1] in _DANGEROUS_EXTENSIONS:
        raise MediaContentValidationError("Filename extension is not allowed.", code="UNSUPPORTED_MEDIA_TYPE")

    return clean[:MAX_DISPLAY_FILENAME_LENGTH]


def normalize_content_type(value: object) -> str:
    content_type = str(value or "").split(";", 1)[0].strip().lower()
    aliases = {
        "image/jpg": "image/jpeg",
        "audio/mp3": "audio/mpeg",
        "audio/x-wav": "audio/wav",
        "video/x-m4v": "video/mp4",
    }
    return aliases.get(content_type, content_type)


def normalize_checksum(value: object) -> str:
    checksum = str(value or "").strip().lower()
    if not checksum:
        return ""
    if checksum.startswith("sha256:"):
        checksum = checksum.removeprefix("sha256:")
    if not _SHA256_RE.fullmatch(checksum):
        raise MediaContentValidationError("Checksum must be a SHA-256 hex digest.", code="INVALID_CHECKSUM")
    return checksum


def validate_filename_extension(filename: str, allowed_extensions: frozenset[str]) -> str:
    suffix = os.path.splitext(filename)[1].lower()
    if not suffix or suffix not in allowed_extensions:
        raise MediaContentValidationError("File extension is not allowed.", code="UNSUPPORTED_MEDIA_TYPE")
    return suffix


def sniff_content_type(head: bytes) -> str | None:
    data = bytes(head or b"")
    if len(data) >= 3 and data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand == b"qt  ":
            return "video/quicktime"
        if brand in {b"M4A ", b"M4B ", b"M4P ", b"F4A "}:
            return "audio/mp4"
        return "video/mp4"
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0:
        return "audio/aac"
    if data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"#!") or data.startswith(b"MZ") or data.startswith(b"\x7fELF"):
        return "application/x-executable"
    return None


def validate_magic_type(
    *,
    head: bytes,
    claimed_content_type: str,
    allowed_content_types: frozenset[str],
) -> str:
    claimed = normalize_content_type(claimed_content_type)
    detected = normalize_content_type(sniff_content_type(head) or "")
    if detected == "application/x-executable":
        raise MediaContentValidationError("Executable content is not allowed.", code="INVALID_FILE")
    if not detected:
        raise MediaContentValidationError("File content could not be verified.", code="INVALID_FILE")
    if detected not in allowed_content_types:
        raise MediaContentValidationError("File content type is not allowed.", code="UNSUPPORTED_MEDIA_TYPE")
    if claimed and claimed != detected:
        compatible = (
            {claimed, detected} <= {"video/mp4", "video/quicktime"}
            or {claimed, detected} <= {"audio/mp4", "video/mp4"}
        )
        if not compatible:
            raise MediaContentValidationError("File content does not match its declared type.", code="INVALID_FILE")
    return detected



def extract_iso_bmff_duration_seconds(
    *,
    read_range: Callable[[int, int], bytes],
    size_bytes: int,
    max_duration_seconds: float | None = None,
) -> float:
    """Read authoritative MP4/MOV container duration with bounded range I/O.

    The parser intentionally understands only the ISO-BMFF box structure needed
    for ``moov/mvhd``. It does not invoke a shell, trust client metadata, or load
    a large video into worker memory. Malformed/fragment-only containers without
    an authoritative movie-header duration are rejected at the Media boundary.
    """

    total = int(size_bytes or 0)
    if total < 16:
        raise MediaContentValidationError("Video container is malformed.", code="INVALID_FILE")

    def read_exact(offset: int, length: int) -> bytes:
        if offset < 0 or length <= 0 or offset + length > total:
            raise MediaContentValidationError("Video container is malformed.", code="INVALID_FILE")
        data = bytes(read_range(offset, length) or b"")
        if len(data) != length:
            raise MediaContentValidationError("Video container is incomplete.", code="UPLOAD_INCOMPLETE")
        return data

    def box_header(offset: int, parent_end: int) -> tuple[bytes, int, int]:
        if parent_end - offset < 8:
            raise MediaContentValidationError("Video container is malformed.", code="INVALID_FILE")
        header = read_exact(offset, 8)
        box_size = int.from_bytes(header[:4], "big")
        box_type = header[4:8]
        header_size = 8
        if box_size == 1:
            if parent_end - offset < 16:
                raise MediaContentValidationError("Video container is malformed.", code="INVALID_FILE")
            box_size = int.from_bytes(read_exact(offset + 8, 8), "big")
            header_size = 16
        elif box_size == 0:
            box_size = parent_end - offset
        if box_size < header_size or offset + box_size > parent_end:
            raise MediaContentValidationError("Video container is malformed.", code="INVALID_FILE")
        return box_type, int(box_size), header_size

    moov_start = None
    moov_end = None
    offset = 0
    for _ in range(128):
        if offset >= total:
            break
        box_type, box_size, header_size = box_header(offset, total)
        if box_type == b"moov":
            moov_start = offset + header_size
            moov_end = offset + box_size
            break
        offset += box_size
    if moov_start is None or moov_end is None:
        raise MediaContentValidationError("Video metadata could not be verified.", code="INVALID_FILE")

    offset = moov_start
    for _ in range(256):
        if offset >= moov_end:
            break
        box_type, box_size, header_size = box_header(offset, moov_end)
        if box_type != b"mvhd":
            offset += box_size
            continue
        payload_offset = offset + header_size
        payload_size = box_size - header_size
        if payload_size < 20:
            raise MediaContentValidationError("Video metadata is malformed.", code="INVALID_FILE")
        version_and_flags = read_exact(payload_offset, 4)
        version = version_and_flags[0]
        if version == 0:
            if payload_size < 20:
                raise MediaContentValidationError("Video metadata is malformed.", code="INVALID_FILE")
            values = read_exact(payload_offset + 12, 8)
            timescale = int.from_bytes(values[:4], "big")
            duration_units = int.from_bytes(values[4:8], "big")
        elif version == 1:
            if payload_size < 32:
                raise MediaContentValidationError("Video metadata is malformed.", code="INVALID_FILE")
            values = read_exact(payload_offset + 20, 12)
            timescale = int.from_bytes(values[:4], "big")
            duration_units = int.from_bytes(values[4:12], "big")
        else:
            raise MediaContentValidationError("Video metadata version is unsupported.", code="INVALID_FILE")
        if timescale <= 0 or duration_units <= 0:
            raise MediaContentValidationError("Video duration is invalid.", code="INVALID_FILE")
        duration = float(duration_units) / float(timescale)
        if duration <= 0 or duration > 24 * 60 * 60:
            raise MediaContentValidationError("Video duration is invalid.", code="INVALID_FILE")
        if max_duration_seconds and duration > float(max_duration_seconds) + 0.05:
            raise MediaContentValidationError("Video duration exceeds the allowed limit.", code="DURATION_EXCEEDED")
        return duration

    raise MediaContentValidationError("Video duration metadata is missing.", code="INVALID_FILE")

def validate_image_bytes(
    payload: bytes,
    *,
    expected_content_type: str,
    min_width: int | None = None,
    min_height: int | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[int, int]:
    if Image is None:
        raise MediaContentValidationError(
            "Image content validation is unavailable.",
            code="INVALID_FILE",
        )
    if not payload:
        raise MediaContentValidationError("Image is empty.", code="INVALID_FILE")

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(payload))
            width, height = image.size
            # Check our hard decoded-pixel budget before verification or any full
            # pixel decode. Avoid mutating Pillow's process-global MAX_IMAGE_PIXELS
            # so concurrent Frappe requests cannot race on a security setting.
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                raise MediaContentValidationError("Image dimensions are too large.", code="INVALID_FILE")
            if bool(getattr(image, "is_animated", False)):
                raise MediaContentValidationError(
                    "Animated images are not supported.",
                    code="INVALID_FILE",
                )
            format_name = str(image.format or "").upper()
            image.verify()
            image = Image.open(io.BytesIO(payload))
            if ImageOps is not None:
                image = ImageOps.exif_transpose(image)
            width, height = image.size
    except MediaContentValidationError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise MediaContentValidationError("Image content is malformed.", code="INVALID_FILE") from exc

    format_types = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    detected = format_types.get(format_name)
    if detected and normalize_content_type(expected_content_type) != detected:
        raise MediaContentValidationError("Image format does not match its declared type.", code="INVALID_FILE")
    if width <= 0 or height <= 0:
        raise MediaContentValidationError("Image dimensions are invalid.", code="INVALID_FILE")
    if width * height > MAX_IMAGE_PIXELS:
        raise MediaContentValidationError("Image dimensions are too large.", code="INVALID_FILE")
    if (min_width and width < min_width) or (min_height and height < min_height):
        raise MediaContentValidationError("Image dimensions are too small.", code="INVALID_FILE")
    if (max_width and width > max_width) or (max_height and height > max_height):
        raise MediaContentValidationError("Image dimensions are too large.", code="INVALID_FILE")
    return int(width), int(height)


def sanitize_public_image_bytes(
    payload: bytes,
    *,
    expected_content_type: str,
    min_width: int | None = None,
    min_height: int | None = None,
    max_width: int | None = None,
    max_height: int | None = None,
) -> tuple[bytes, int, int]:
    """Re-encode a public marketplace image without EXIF/GPS/user metadata.

    Public uploads are intentionally canonicalized before entering the public
    bucket. Re-encoding also applies EXIF orientation to pixels, so callers can
    safely expose immutable CDN URLs without retaining GPS/camera metadata or
    browser-interpretable ancillary payloads.
    """
    width, height = validate_image_bytes(
        payload,
        expected_content_type=expected_content_type,
        min_width=min_width,
        min_height=min_height,
        max_width=max_width,
        max_height=max_height,
    )
    if Image is None:
        raise MediaContentValidationError("Image processing is unavailable.", code="INVALID_FILE")

    content_type = normalize_content_type(expected_content_type)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(payload))
            if bool(getattr(image, "is_animated", False)):
                raise MediaContentValidationError("Animated images are not supported.", code="INVALID_FILE")
            if ImageOps is not None:
                image = ImageOps.exif_transpose(image)
            image.load()
            output = io.BytesIO()
            if content_type == "image/jpeg":
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(
                    output,
                    format="JPEG",
                    quality=90,
                    optimize=True,
                    progressive=True,
                )
            elif content_type == "image/png":
                if image.mode not in {"1", "L", "LA", "P", "RGB", "RGBA"}:
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                image.save(output, format="PNG", optimize=True)
            elif content_type == "image/webp":
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                image.save(output, format="WEBP", quality=88, method=4)
            else:
                raise MediaContentValidationError("Unsupported public image type.", code="UNSUPPORTED_MEDIA_TYPE")
            sanitized = output.getvalue()
    except MediaContentValidationError:
        raise
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise MediaContentValidationError("Image could not be safely normalized.", code="INVALID_FILE") from exc

    clean_width, clean_height = validate_image_bytes(
        sanitized,
        expected_content_type=content_type,
        min_width=min_width,
        min_height=min_height,
        max_width=max_width,
        max_height=max_height,
    )
    if (clean_width, clean_height) != (width, height):
        raise MediaContentValidationError("Image dimensions changed unexpectedly.", code="INVALID_FILE")
    return sanitized, clean_width, clean_height


def sha256_chunks(
    chunks: Iterable[bytes],
    *,
    content_type: str | None = None,
) -> tuple[str, int]:
    """Hash a stream and reject active PDF constructs without buffering it."""
    digest = hashlib.sha256()
    total = 0
    tail = b""
    is_pdf = normalize_content_type(content_type) == "application/pdf"
    dangerous_pdf_tokens = (
        b"/javascript",
        b"/js",
        b"/launch",
        b"/embeddedfile",
        b"/openaction",
        b"/aa",
    )
    for chunk in chunks:
        block = bytes(chunk or b"")
        total += len(block)
        digest.update(block)
        if is_pdf:
            window = (tail + block).lower()
            if any(token in window for token in dangerous_pdf_tokens):
                raise MediaContentValidationError(
                    "PDF contains unsupported active content.",
                    code="INVALID_FILE",
                )
            tail = window[-64:]
    return digest.hexdigest(), total
