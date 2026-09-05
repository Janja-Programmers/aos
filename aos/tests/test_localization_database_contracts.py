from __future__ import annotations

import frappe
from frappe.tests.utils import FrappeTestCase

from aos.patches.v1_0 import install_localization_schema


class TestLocalizationDatabaseContracts(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		install_localization_schema.execute()
		frappe.db.commit()

	def test_required_location_and_preference_indexes_exist(self):
		self.assertTrue(
			self._has_exact_index(
				"AOS Location",
				install_localization_schema.LOCATION_UNIQUE_FIELDS,
				unique=True,
			)
		)
		self.assertTrue(
			self._has_exact_index(
				"AOS Location",
				install_localization_schema.LOCATION_READ_INDEX_FIELDS,
			)
		)
		self.assertTrue(
			self._has_exact_index(
				"AOS User Preference",
				install_localization_schema.PREFERENCE_UNIQUE_FIELDS,
				unique=True,
			)
		)

	def test_localization_schema_patch_is_idempotent(self):
		install_localization_schema.execute()
		install_localization_schema.execute()
		self.assertTrue(
			self._has_exact_index(
				"AOS Location",
				install_localization_schema.LOCATION_READ_INDEX_FIELDS,
			)
		)

	@staticmethod
	def _has_exact_index(doctype: str, fields: tuple[str, ...], *, unique: bool | None = None) -> bool:
		rows = frappe.db.sql(
			"""SELECT INDEX_NAME, NON_UNIQUE, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) columns_csv
			FROM information_schema.STATISTICS
			WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s
			GROUP BY INDEX_NAME, NON_UNIQUE""",
			(f"tab{doctype}",),
			as_dict=True,
		)
		expected = ",".join(fields)
		for row in rows:
			if row.columns_csv != expected:
				continue
			if unique is None or (int(row.NON_UNIQUE or 0) == 0) == unique:
				return True
		return False
