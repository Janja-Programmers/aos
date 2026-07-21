from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import harden_localization_indexes


class TestLocalizationDatabaseContracts(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		harden_localization_indexes.execute()
		frappe.db.commit()

	def test_location_pagination_index_exists(self):
		self.assertTrue(self._index_exists(harden_localization_indexes.INDEX_NAME))

	def test_location_pagination_index_patch_is_idempotent(self):
		harden_localization_indexes.execute()
		harden_localization_indexes.execute()
		self.assertTrue(self._index_exists(harden_localization_indexes.INDEX_NAME))

	@staticmethod
	def _index_exists(index_name: str) -> bool:
		return bool(
			frappe.db.sql(
				"""
				SELECT INDEX_NAME
				FROM information_schema.STATISTICS
				WHERE TABLE_SCHEMA = DATABASE()
				  AND TABLE_NAME = 'tabAOS Location'
				  AND INDEX_NAME = %s
				LIMIT 1
				""",
				(index_name,),
			)
		)
