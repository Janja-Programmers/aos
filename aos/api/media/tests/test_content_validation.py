from __future__ import annotations

import io
from pathlib import Path
from unittest import TestCase

from PIL import Image

from aos.services.media.content_validation import (
	MediaContentValidationError,
	extract_iso_bmff_duration_seconds,
	normalize_checksum,
	normalize_filename,
	sha256_chunks,
	sniff_content_type,
	validate_filename_extension,
	validate_image_bytes,
	validate_magic_type,
	sanitize_public_image_bytes,
)

PNG_64 = (Path(__file__).parent / "fixtures" / "valid_64x64.png").read_bytes()



def _box(box_type: bytes, payload: bytes) -> bytes:
	return (8 + len(payload)).to_bytes(4, "big") + box_type + payload


def _movie_bytes(*, duration_units: int = 12500, timescale: int = 1000, version: int = 0) -> bytes:
	ftyp = _box(b"ftyp", b"isom" + b"\x00" * 12)
	if version == 0:
		mvhd_payload = (
			b"\x00\x00\x00\x00"
			+ b"\x00" * 8
			+ int(timescale).to_bytes(4, "big")
			+ int(duration_units).to_bytes(4, "big")
			+ b"\x00" * 12
		)
	else:
		mvhd_payload = (
			b"\x01\x00\x00\x00"
			+ b"\x00" * 16
			+ int(timescale).to_bytes(4, "big")
			+ int(duration_units).to_bytes(8, "big")
			+ b"\x00" * 12
		)
	return ftyp + _box(b"moov", _box(b"mvhd", mvhd_payload))


class TestMediaContentValidation(TestCase):
	def test_normalize_filename_nfkc_and_sanitizes_display_metadata(self):
		self.assertEqual(normalize_filename("  café photo (1).PNG  "), "café photo (1).PNG")
		self.assertEqual(normalize_filename("Ｆｏｏ.png"), "Foo.png")  # noqa: RUF001

	def test_rejects_path_traversal_null_and_hidden_filenames(self):
		filenames = [
			"../avatar.jpg",
			"folder/avatar.jpg",
			"folder\\avatar.jpg",
			"bad\x00.jpg",
			".hidden.jpg",
		]
		for filename in filenames:
			with self.subTest(filename=filename):
				with self.assertRaises(MediaContentValidationError) as context:
					normalize_filename(filename)
				self.assertEqual(context.exception.code, "INVALID_FILENAME")

	def test_rejects_dangerous_double_extensions(self):
		for filename in ["invoice.pdf.exe", "photo.php.jpg", "avatar.jpg.js"]:
			with self.subTest(filename=filename):
				with self.assertRaises(MediaContentValidationError):
					normalize_filename(filename)

	def test_extension_allowlist_is_exact(self):
		self.assertEqual(
			validate_filename_extension("avatar.JPEG", frozenset({".jpg", ".jpeg"})),
			".jpeg",
		)
		with self.assertRaises(MediaContentValidationError) as context:
			validate_filename_extension("avatar.gif", frozenset({".jpg", ".jpeg"}))
		self.assertEqual(context.exception.code, "UNSUPPORTED_MEDIA_TYPE")

	def test_magic_validation_accepts_real_png_and_rejects_spoofing(self):
		self.assertEqual(sniff_content_type(PNG_64), "image/png")
		self.assertEqual(
			validate_magic_type(
				head=PNG_64,
				claimed_content_type="image/png",
				allowed_content_types=frozenset({"image/png"}),
			),
			"image/png",
		)
		with self.assertRaises(MediaContentValidationError) as context:
			validate_magic_type(
				head=PNG_64,
				claimed_content_type="image/jpeg",
				allowed_content_types=frozenset({"image/jpeg", "image/png"}),
			)
		self.assertEqual(context.exception.code, "INVALID_FILE")

	def test_magic_validation_rejects_executable_and_unknown_content(self):
		with self.assertRaises(MediaContentValidationError):
			validate_magic_type(
				head=b"MZ" + b"\x00" * 100,
				claimed_content_type="image/jpeg",
				allowed_content_types=frozenset({"image/jpeg"}),
			)
		with self.assertRaises(MediaContentValidationError):
			validate_magic_type(
				head=b"not media",
				claimed_content_type="image/jpeg",
				allowed_content_types=frozenset({"image/jpeg"}),
			)

	def test_real_image_metadata_is_verified(self):
		self.assertEqual(
			validate_image_bytes(PNG_64, expected_content_type="image/png"),
			(64, 64),
		)
		with self.assertRaises(MediaContentValidationError):
			validate_image_bytes(
				b"\x89PNG\r\n\x1a\nmalformed",
				expected_content_type="image/png",
			)
		with self.assertRaises(MediaContentValidationError):
			validate_image_bytes(
				PNG_64,
				expected_content_type="image/png",
				min_width=65,
			)

	def test_pdf_stream_rejects_active_content_across_chunk_boundaries(self):
		chunks = [b"%PDF-1.7\n1 0 obj << /Open", b"Action 2 0 R >>\nendobj"]
		with self.assertRaises(MediaContentValidationError) as context:
			sha256_chunks(chunks, content_type="application/pdf")
		self.assertEqual(context.exception.code, "INVALID_FILE")

	def test_pdf_stream_hashes_benign_content(self):
		checksum, size = sha256_chunks(
			[b"%PDF-1.4\n1 0 obj << /Type /Catalog >>\nendobj\n%%EOF"],
			content_type="application/pdf",
		)
		self.assertEqual(len(checksum), 64)
		self.assertGreater(size, 0)

	def test_checksum_normalization_accepts_only_sha256(self):
		digest = "a" * 64
		self.assertEqual(normalize_checksum(f"sha256:{digest}"), digest)
		with self.assertRaises(MediaContentValidationError) as context:
			normalize_checksum("deadbeef")
		self.assertEqual(context.exception.code, "INVALID_CHECKSUM")

	def test_public_image_sanitization_strips_exif_metadata(self):
		image = Image.new("RGB", (32, 24), (120, 80, 40))
		exif = Image.Exif()
		exif[274] = 1
		exif[315] = "sensitive-camera-owner"
		output = io.BytesIO()
		image.save(output, format="JPEG", quality=95, exif=exif)
		source = output.getvalue()

		with Image.open(io.BytesIO(source)) as original:
			self.assertEqual(original.getexif().get(315), "sensitive-camera-owner")

		sanitized, width, height = sanitize_public_image_bytes(
			source, expected_content_type="image/jpeg"
		)
		self.assertEqual((width, height), (32, 24))
		self.assertNotEqual(sanitized, source)
		with Image.open(io.BytesIO(sanitized)) as result:
			self.assertFalse(result.getexif())
			self.assertEqual(result.size, (32, 24))

	def test_image_pixel_limit_is_enforced_as_a_hard_failure(self):
		import aos.services.media.content_validation as validation

		output = io.BytesIO()
		Image.new("RGB", (20, 20), (0, 0, 0)).save(output, format="PNG")
		previous = validation.MAX_IMAGE_PIXELS
		validation.MAX_IMAGE_PIXELS = 100
		try:
			with self.assertRaises(MediaContentValidationError):
				validate_image_bytes(output.getvalue(), expected_content_type="image/png")
		finally:
			validation.MAX_IMAGE_PIXELS = previous

	def test_iso_bmff_duration_is_read_from_authoritative_container_metadata(self):
		payload = _movie_bytes(duration_units=12500, timescale=1000)
		duration = extract_iso_bmff_duration_seconds(
			read_range=lambda offset, length: payload[offset : offset + length],
			size_bytes=len(payload),
			max_duration_seconds=20,
		)
		self.assertAlmostEqual(duration, 12.5)

	def test_iso_bmff_duration_supports_version_one_movie_header(self):
		payload = _movie_bytes(duration_units=90_000, timescale=30_000, version=1)
		duration = extract_iso_bmff_duration_seconds(
			read_range=lambda offset, length: payload[offset : offset + length],
			size_bytes=len(payload),
			max_duration_seconds=10,
		)
		self.assertAlmostEqual(duration, 3.0)

	def test_iso_bmff_duration_rejects_oversized_or_missing_metadata(self):
		payload = _movie_bytes(duration_units=601_000, timescale=1000)
		with self.assertRaises(MediaContentValidationError) as context:
			extract_iso_bmff_duration_seconds(
				read_range=lambda offset, length: payload[offset : offset + length],
				size_bytes=len(payload),
				max_duration_seconds=600,
			)
		self.assertEqual(context.exception.code, "DURATION_EXCEEDED")

		invalid = _box(b"ftyp", b"isom" + b"\x00" * 12)
		with self.assertRaises(MediaContentValidationError):
			extract_iso_bmff_duration_seconds(
				read_range=lambda offset, length: invalid[offset : offset + length],
				size_bytes=len(invalid),
			)
