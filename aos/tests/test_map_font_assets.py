from __future__ import annotations

import json
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase


class TestMapFontAssets(FrappeTestCase):
	"""Keep TileServer glyph requirements synchronized across deployment files."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.root = Path(__file__).resolve().parents[2]
		cls.style_path = cls.root / "infra/maps/tileserver/styles/aos/style.json"
		cls.style = json.loads(cls.style_path.read_text(encoding="utf-8"))

	def tearDown(self):
		frappe.local.response = {}
		super().tearDown()

	def test_style_uses_literal_maplibre_glyph_tokens(self):
		glyphs_url = self.style["glyphs"]
		self.assertTrue(glyphs_url.startswith("https://"))
		self.assertTrue(glyphs_url.endswith("/fonts/{fontstack}/{range}.pbf"))
		self.assertNotIn("%7Bfontstack%7D", glyphs_url)
		self.assertNotIn("%7Brange%7D", glyphs_url)

	def test_font_builder_covers_every_style_font_stack(self):
		font_stacks: set[str] = set()
		for layer in self.style["layers"]:
			text_font = layer.get("layout", {}).get("text-font")
			if isinstance(text_font, list):
				font_stacks.update(value for value in text_font if isinstance(value, str))

		builder = (self.root / "infra/maps/scripts/build-map-fonts.sh").read_text(encoding="utf-8")
		self.assertEqual(font_stacks, {"Noto Sans Regular", "Noto Sans Bold"})
		for font_stack in font_stacks:
			self.assertIn(f'  "{font_stack}"', builder)

	def test_tileserver_healthcheck_requires_regular_and_bold_glyphs(self):
		compose = (self.root / "docker-compose.yml").read_text(encoding="utf-8")
		self.assertIn("fonts/Noto%20Sans%20Regular/0-255.pbf", compose)
		self.assertIn("fonts/Noto%20Sans%20Bold/0-255.pbf", compose)

	def test_map_verifier_rejects_missing_glyph_assets(self):
		verifier = (self.root / "infra/maps/scripts/verify-map-data.sh").read_text(encoding="utf-8")
		self.assertIn('build-map-fonts.sh" --verify-only', verifier)
		self.assertIn("Map glyph assets are missing or incomplete", verifier)

	def test_font_mount_directory_is_tracked(self):
		self.assertTrue((self.root / "infra/maps/tileserver/fonts/.gitkeep").is_file())
