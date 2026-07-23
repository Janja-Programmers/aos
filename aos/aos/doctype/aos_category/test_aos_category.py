# Copyright (c) 2026, Africa Online Stores and Contributors
# See license.txt

from frappe.tests import IntegrationTestCase
from frappe.utils.nestedset import NestedSet

from aos.aos.doctype.aos_category.aos_category import AOSCategory


class IntegrationTestAOSCategory(IntegrationTestCase):
	"""Controller-level contracts that do not duplicate API/domain flow tests."""

	def test_controller_preserves_nested_set_behavior(self):
		self.assertTrue(issubclass(AOSCategory, NestedSet))
