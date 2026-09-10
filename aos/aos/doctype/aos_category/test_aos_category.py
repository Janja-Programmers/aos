# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

import json
from pathlib import Path

import frappe
from frappe.model.document import Document
from frappe.tests import IntegrationTestCase

from aos.aos.doctype.aos_category.aos_category import AOSCategory


class IntegrationTestAOSCategory(IntegrationTestCase):
    def test_controller_uses_plain_document_not_nested_set(self):
        self.assertTrue(issubclass(AOSCategory, Document))
        self.assertFalse(frappe.get_meta("AOS Category").is_tree)

    def test_desk_media_metadata_is_canonical(self):
        meta = frappe.get_meta("AOS Category")
        self.assertEqual(meta.allow_rename, 0)
        self.assertEqual(meta.get_field("icon_preview").fieldtype, "HTML")
        image_media = meta.get_field("image_media")
        self.assertEqual(image_media.fieldtype, "Link")
        self.assertEqual(image_media.options, "AOS Media Object")
        self.assertEqual(image_media.read_only, 1)
        self.assertEqual(image_media.hidden, 1)
        self.assertEqual(image_media.no_copy, 1)
        self.assertIsNone(meta.get_field("icon"))
        self.assertIsNone(meta.get_field("icon_media"))
        self.assertIsNone(meta.get_field("lft"))
        self.assertIsNone(meta.get_field("rgt"))

    def test_source_controlled_permission_defaults_remain_effective(self):
        schema_path = Path(
            frappe.get_app_path("aos", "aos", "doctype", "aos_category", "aos_category.json")
        )
        source_permissions = json.loads(schema_path.read_text(encoding="utf-8"))["permissions"]
        source_roles = {row["role"] for row in source_permissions}
        effective_roles = {permission.role for permission in frappe.get_meta("AOS Category").permissions}

        self.assertTrue(source_roles)
        self.assertTrue(source_roles.issubset(effective_roles))
        for row in source_permissions:
            self.assertTrue(row.get("read"))
            self.assertTrue(row.get("write"))
            self.assertTrue(row.get("create"))
            self.assertTrue(row.get("delete"))
