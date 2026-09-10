# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase


class IntegrationTestAOSAdAttribute(IntegrationTestCase):
    def test_identity_and_permission_metadata(self):
        meta = frappe.get_meta("AOS Ad Attribute")
        self.assertEqual(meta.allow_rename, 0)
        key = meta.get_field("attribute_key")
        self.assertEqual(key.fieldtype, "Data")
        self.assertEqual(key.read_only, 1)
        self.assertEqual(key.unique, 1)
        schema_path = Path(
            frappe.get_app_path(
                "aos", "aos", "doctype", "aos_ad_attribute", "aos_ad_attribute.json"
            )
        )
        source_permissions = json.loads(schema_path.read_text(encoding="utf-8"))["permissions"]
        source_roles = {row["role"] for row in source_permissions}
        effective_roles = {permission.role for permission in meta.permissions}
        self.assertTrue(source_roles)
        self.assertTrue(source_roles.issubset(effective_roles))
        for row in source_permissions:
            self.assertTrue(row.get("read"))
            self.assertTrue(row.get("write"))
            self.assertTrue(row.get("create"))
            self.assertTrue(row.get("delete"))
