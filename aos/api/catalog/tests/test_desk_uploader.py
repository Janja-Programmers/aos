from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from aos.services.media.media_purposes import MEDIA_PURPOSES


_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_CATEGORY_DIRECTORY = _REPOSITORY_ROOT / "aos" / "aos" / "doctype" / "aos_category"
_CATEGORY_JSON = _CATEGORY_DIRECTORY / "aos_category.json"
_CATEGORY_JS = _CATEGORY_DIRECTORY / "aos_category.js"


class TestCatalogDeskUploader(TestCase):
    def test_category_form_exposes_uploader_and_protects_raw_media_fields(self):
        definition = json.loads(_CATEGORY_JSON.read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in definition["fields"]}

        self.assertEqual(fields["icon_preview"]["fieldtype"], "HTML")
        self.assertEqual(fields["category_image_section"]["fieldtype"], "Section Break")
        self.assertEqual(fields["icon_media"]["fieldtype"], "Link")
        self.assertEqual(fields["icon_media"].get("read_only"), 1)
        self.assertEqual(fields["icon"].get("read_only"), 1)
        self.assertEqual(fields["icon"].get("hidden"), 1)
        self.assertLess(
            definition["field_order"].index("icon_preview"),
            definition["field_order"].index("icon_media"),
        )

    def test_desk_uploader_uses_only_the_hardened_media_pipeline(self):
        script = _CATEGORY_JS.read_text(encoding="utf-8")

        self.assertIn('"aos.api.v1.media.init_upload"', script)
        self.assertIn('"aos.api.v1.media.confirm_upload"', script)
        self.assertIn('"aos.api.v1.media.delete_media"', script)
        self.assertIn('xhr.open("PUT", uploadUrl, true)', script)
        self.assertIn('cryptoApi.subtle.digest("SHA-256"', script)
        self.assertIn("idempotency_key: idempotencyKey", script)
        self.assertIn("function uploadIdempotencyKey", script)
        self.assertNotIn(').join(":")', script)
        self.assertIn('frappe.user_roles.includes("System Manager")', script)
        self.assertNotIn("upload_file", script)
        self.assertNotIn("frappe.ui.FileUploader", script)

    def test_client_hints_are_locked_to_the_central_category_icon_policy(self):
        policy = MEDIA_PURPOSES["category_icon"]
        script = _CATEGORY_JS.read_text(encoding="utf-8")

        self.assertEqual(policy.max_size_bytes, 5 * 1024 * 1024)
        self.assertEqual(policy.min_width, 16)
        self.assertEqual(policy.min_height, 16)
        self.assertEqual(policy.max_width, 4096)
        self.assertEqual(policy.max_height, 4096)
        self.assertIn("maxSizeBytes: 5 * 1024 * 1024", script)
        self.assertIn("minWidth: 16", script)
        self.assertIn("minHeight: 16", script)
        self.assertIn("maxWidth: 4096", script)
        self.assertIn("maxHeight: 4096", script)
        for content_type in sorted(policy.allowed_content_types):
            self.assertIn(f'"{content_type}"', script)
        for extension in sorted(policy.allowed_extensions):
            self.assertIn(f'"{extension}"', script)

    def test_desk_uploader_has_replacement_removal_and_failure_cleanup(self):
        script = _CATEGORY_JS.read_text(encoding="utf-8")

        self.assertIn('__("Replace image")', script)
        self.assertIn('__("Remove image")', script)
        self.assertIn("deleteUnattachedMedia(mediaId)", script)
        self.assertIn('await frm.set_value("icon_media", "")', script)
        self.assertIn("await frm.save()", script)
        self.assertIn("frappe.confirm(", script)
