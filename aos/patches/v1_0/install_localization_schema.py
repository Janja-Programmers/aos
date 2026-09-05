"""Install the final Localization database constraints and hot-path index.

This patch targets the current AOS schema on a clean/new site. DocType model sync
runs first; the patch only installs database structures that cannot be expressed
as the authoritative DocType field metadata (notably the composite location
constraint/index) and defensively verifies the per-user preference uniqueness.
"""

from __future__ import annotations

import frappe

LOCATION_UNIQUE_NAME = "unique_aos_location_country_location"
LOCATION_UNIQUE_FIELDS = ("country", "location")
LOCATION_READ_INDEX_NAME = "idx_aos_location_country_active_order"
LOCATION_READ_INDEX_FIELDS = ("country", "is_active", "sort_order", "location")
PREFERENCE_UNIQUE_NAME = "unique_aos_user_preference_user"
PREFERENCE_UNIQUE_FIELDS = ("user",)


def execute() -> None:
	_ensure_unique("AOS Location", LOCATION_UNIQUE_FIELDS, LOCATION_UNIQUE_NAME)
	_ensure_index("AOS Location", LOCATION_READ_INDEX_FIELDS, LOCATION_READ_INDEX_NAME)
	_ensure_unique("AOS User Preference", PREFERENCE_UNIQUE_FIELDS, PREFERENCE_UNIQUE_NAME)


def _index_rows(doctype: str):
	return frappe.db.sql(
		"""
		SELECT INDEX_NAME, NON_UNIQUE,
		       GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS columns_csv
		FROM information_schema.STATISTICS
		WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
		GROUP BY INDEX_NAME, NON_UNIQUE
		""",
		(f"tab{doctype}",),
		as_dict=True,
	)


def _has_exact_index(doctype: str, fields: tuple[str, ...], *, unique: bool | None = None) -> bool:
	expected = ",".join(fields)
	for row in _index_rows(doctype):
		if row.columns_csv != expected:
			continue
		is_unique = int(row.NON_UNIQUE or 0) == 0
		if unique is None or is_unique == unique:
			return True
	return False


def _ensure_unique(doctype: str, fields: tuple[str, ...], name: str) -> None:
	if not frappe.db.table_exists(doctype) or _has_exact_index(doctype, fields, unique=True):
		return
	frappe.db.add_unique(doctype, list(fields), constraint_name=name)


def _ensure_index(doctype: str, fields: tuple[str, ...], name: str) -> None:
	if not frappe.db.table_exists(doctype) or _has_exact_index(doctype, fields):
		return
	frappe.db.add_index(doctype, list(fields), index_name=name)
