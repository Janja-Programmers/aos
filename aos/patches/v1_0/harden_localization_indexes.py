"""Add the composite index used by active location pagination queries."""

from __future__ import annotations

import frappe

INDEX_NAME = "idx_aos_location_country_active_order"
INDEX_FIELDS = ["country", "is_active", "sort_order", "location"]


def execute():
	if not frappe.db.exists("DocType", "AOS Location") or _index_exists():
		return
	frappe.db.add_index("AOS Location", INDEX_FIELDS, index_name=INDEX_NAME)


def _index_exists() -> bool:
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
			(INDEX_NAME,),
		)
	)
