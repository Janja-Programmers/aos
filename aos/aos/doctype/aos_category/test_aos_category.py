# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.nestedset import NestedSet

from aos.aos.doctype.aos_category.aos_category import AOSCategory


class IntegrationTestAOSCategory(IntegrationTestCase):
	"""Controller-level contracts that do not duplicate API/domain flow tests."""

	def test_controller_preserves_nested_set_behavior(self):
		self.assertTrue(issubclass(AOSCategory, NestedSet))

	def test_desk_uploader_metadata_is_synced(self):
		meta = frappe.get_meta("AOS Category")
		self.assertEqual(meta.get_field("icon_preview").fieldtype, "HTML")
		self.assertEqual(meta.get_field("icon_media").read_only, 1)
		self.assertEqual(meta.get_field("icon").read_only, 1)
		self.assertEqual(meta.get_field("icon").hidden, 1)
