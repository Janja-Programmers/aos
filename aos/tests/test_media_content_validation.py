from __future__ import annotations

from pathlib import Path
from unittest import TestCase

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

PNG_64 = (Path(__file__).parent / "fixtures" / "media" / "valid_64x64.png").read_bytes()


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
