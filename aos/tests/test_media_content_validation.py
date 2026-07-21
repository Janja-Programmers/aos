from __future__ import annotations

import base64

import pytest

from aos.services.media.content_validation import (
    MediaContentValidationError,
    normalize_checksum,
    normalize_filename,
    sha256_chunks,
    sniff_content_type,
    validate_filename_extension,
    validate_image_bytes,
    validate_magic_type,
)

PNG_64 = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAIAAAAlC+aJAAAAZElEQVR4nO3PsQ3AIADAMGBkZub/M3sEg1UpviCZ+9zxZ0sHvGpAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtAa0BrQGtA+wCQDQC85SihYwAAAABJRU5ErkJggg==")


def test_normalize_filename_nfkc_and_sanitizes_display_metadata():
    assert normalize_filename("  café photo (1).PNG  ") == "café photo (1).PNG"
    assert normalize_filename("Ｆｏｏ.png") == "Foo.png"  # noqa: RUF001


@pytest.mark.parametrize(
    "filename",
    ["../avatar.jpg", "folder/avatar.jpg", "folder\\avatar.jpg", "bad\x00.jpg", ".hidden.jpg"],
)
def test_rejects_path_traversal_null_and_hidden_filenames(filename):
    with pytest.raises(MediaContentValidationError) as exc:
        normalize_filename(filename)
    assert exc.value.code == "INVALID_FILENAME"


@pytest.mark.parametrize("filename", ["invoice.pdf.exe", "photo.php.jpg", "avatar.jpg.js"])
def test_rejects_dangerous_double_extensions(filename):
    with pytest.raises(MediaContentValidationError):
        normalize_filename(filename)


def test_extension_allowlist_is_exact():
    assert validate_filename_extension("avatar.JPEG", frozenset({".jpg", ".jpeg"})) == ".jpeg"
    with pytest.raises(MediaContentValidationError) as exc:
        validate_filename_extension("avatar.gif", frozenset({".jpg", ".jpeg"}))
    assert exc.value.code == "UNSUPPORTED_MEDIA_TYPE"


def test_magic_validation_accepts_real_png_and_rejects_spoofing():
    assert sniff_content_type(PNG_64) == "image/png"
    assert (
        validate_magic_type(
            head=PNG_64,
            claimed_content_type="image/png",
            allowed_content_types=frozenset({"image/png"}),
        )
        == "image/png"
    )
    with pytest.raises(MediaContentValidationError) as exc:
        validate_magic_type(
            head=PNG_64,
            claimed_content_type="image/jpeg",
            allowed_content_types=frozenset({"image/jpeg", "image/png"}),
        )
    assert exc.value.code == "INVALID_FILE"


def test_magic_validation_rejects_executable_and_unknown_content():
    with pytest.raises(MediaContentValidationError):
        validate_magic_type(
            head=b"MZ" + b"\x00" * 100,
            claimed_content_type="image/jpeg",
            allowed_content_types=frozenset({"image/jpeg"}),
        )
    with pytest.raises(MediaContentValidationError):
        validate_magic_type(
            head=b"not media",
            claimed_content_type="image/jpeg",
            allowed_content_types=frozenset({"image/jpeg"}),
        )


def test_real_image_metadata_is_verified():
    assert validate_image_bytes(PNG_64, expected_content_type="image/png") == (64, 64)
    with pytest.raises(MediaContentValidationError):
        validate_image_bytes(b"\x89PNG\r\n\x1a\nmalformed", expected_content_type="image/png")
    with pytest.raises(MediaContentValidationError):
        validate_image_bytes(PNG_64, expected_content_type="image/png", min_width=65)


def test_pdf_stream_rejects_active_content_across_chunk_boundaries():
    chunks = [b"%PDF-1.7\n1 0 obj << /Open", b"Action 2 0 R >>\nendobj"]
    with pytest.raises(MediaContentValidationError) as exc:
        sha256_chunks(chunks, content_type="application/pdf")
    assert exc.value.code == "INVALID_FILE"


def test_pdf_stream_hashes_benign_content():
    checksum, size = sha256_chunks(
        [b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\nendobj\n%%EOF"],
        content_type="application/pdf",
    )
    assert len(checksum) == 64
    assert size > 0


def test_checksum_normalization_accepts_only_sha256():
    digest = "a" * 64
    assert normalize_checksum(f"sha256:{digest}") == digest
    with pytest.raises(MediaContentValidationError) as exc:
        normalize_checksum("deadbeef")
    assert exc.value.code == "INVALID_CHECKSUM"
