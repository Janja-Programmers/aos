from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[4]
CATEGORY_JSON = ROOT / "aos" / "aos" / "doctype" / "aos_category" / "aos_category.json"
CATEGORY_JS = ROOT / "aos" / "aos" / "doctype" / "aos_category" / "aos_category.js"
TREE_JS = ROOT / "aos" / "aos" / "doctype" / "aos_category" / "aos_category_tree.js"


class TestCategoryDeskMediaContract(TestCase):
    def test_doc_type_has_one_canonical_media_relationship(self):
        definition = json.loads(CATEGORY_JSON.read_text())
        fields = {field["fieldname"]: field for field in definition["fields"]}

        self.assertIn("image_media", fields)
        self.assertEqual(fields["image_media"]["fieldtype"], "Link")
        self.assertEqual(fields["image_media"]["options"], "AOS Media Object")
        self.assertEqual(fields["image_media"].get("read_only"), 1)
        self.assertEqual(fields["image_media"].get("hidden"), 1)
        self.assertEqual(fields["image_media"].get("no_copy"), 1)
        self.assertNotIn("icon", fields)
        self.assertNotIn("icon_media", fields)
        self.assertNotIn("lft", fields)
        self.assertNotIn("rgt", fields)
        self.assertFalse(definition.get("is_tree"))
        self.assertEqual(definition.get("allow_rename"), 0)
        self.assertFalse(TREE_JS.exists())

    def test_desk_uploader_uses_only_media_api_and_resolves_preview(self):
        script = CATEGORY_JS.read_text()
        self.assertIn('purpose: "category_icon"', script)
        self.assertIn('getMediaUrl: "aos.api.v1.media.get_media_url"', script)
        self.assertIn('await frm.set_value("image_media", mediaId)', script)
        self.assertIn('await frm.set_value("image_media", "")', script)
        self.assertIn('API.getMediaUrl,', script)
        self.assertIn('"GET"\n      );', script)
        self.assertNotIn('set_value("icon"', script)
        self.assertNotIn("icon_media", script)
        self.assertNotIn("frappe.ui.FileUploader", script)
        self.assertNotIn("/api/method/upload_file", script)
        self.assertNotIn("MinIO", script)
        self.assertNotIn("S3", script)
        self.assertNotIn("bucket", script.lower())
        self.assertNotIn("object_key", script)

    def test_client_policy_matches_media_category_purpose_contract(self):
        script = CATEGORY_JS.read_text()
        self.assertIn("5 * 1024 * 1024", script)
        self.assertIn('"image/jpeg"', script)
        self.assertIn('"image/png"', script)
        self.assertIn('"image/webp"', script)
