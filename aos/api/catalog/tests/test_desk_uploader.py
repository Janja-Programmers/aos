from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[4]
CATEGORY_JSON = ROOT / "aos" / "aos" / "doctype" / "aos_category" / "aos_category.json"
CATEGORY_ATTRIBUTE_JSON = (
    ROOT
    / "aos"
    / "aos"
    / "doctype"
    / "aos_category_attribute_row"
    / "aos_category_attribute_row.json"
)
DEPENDENCY_JSON = (
    ROOT
    / "aos"
    / "aos"
    / "doctype"
    / "aos_category_attribute_dependency_row"
    / "aos_category_attribute_dependency_row.json"
)
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

    def test_dependency_rows_group_children_by_parent_option(self):
        definition = json.loads(DEPENDENCY_JSON.read_text())
        fields = {field["fieldname"]: field for field in definition["fields"]}

        self.assertNotIn("child_option", fields)
        self.assertEqual(fields["child_options"]["fieldtype"], "Small Text")
        self.assertEqual(fields["child_options"]["reqd"], 1)
        self.assertEqual(fields["parent_option"]["fieldtype"], "Data")
        self.assertEqual(fields["mapping_key"]["length"], 64)

    def test_dependency_link_queries_are_scoped_to_category_select_attributes(self):
        script = CATEGORY_JS.read_text()
        self.assertIn('frm.set_query("depends_on_attribute", "attributes"', script)
        self.assertIn('frm.set_query("child_attribute", "attribute_dependencies"', script)
        self.assertIn('field_type: "Select"', script)
        self.assertIn('dependentOnly: true', script)
        self.assertIn('attribute !== exclude', script)

    def test_dependent_rows_use_dependency_mappings_as_the_only_option_source(self):
        definition = json.loads(CATEGORY_ATTRIBUTE_JSON.read_text())
        fields = {field["fieldname"]: field for field in definition["fields"]}
        override = fields["options_override"]

        self.assertEqual(override.get("depends_on"), "eval:!doc.depends_on_attribute")
        self.assertIn("Leave blank for dependent attributes", override.get("description", ""))

        script = CATEGORY_JS.read_text()
        self.assertIn('frappe.ui.form.on("AOS Category Attribute Row"', script)
        self.assertIn('frappe.model.set_value(cdt, cdn, "options_override", "")', script)
        self.assertIn("Dependent options are defined in Attribute Dependencies", script)

    def test_client_policy_matches_media_category_purpose_contract(self):
        script = CATEGORY_JS.read_text()
        self.assertIn("5 * 1024 * 1024", script)
        self.assertIn('"image/jpeg"', script)
        self.assertIn('"image/png"', script)
        self.assertIn('"image/webp"', script)
