# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

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
        roles = {permission.role for permission in meta.permissions}
        self.assertEqual(roles, {"System Manager"})
