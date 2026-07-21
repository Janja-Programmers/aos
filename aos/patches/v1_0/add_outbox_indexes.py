from __future__ import annotations

import frappe


def _index_exists(table: str, name: str) -> bool:
	rows = frappe.db.sql(f"SHOW INDEX FROM `{table}` WHERE Key_name = %s", (name,), as_dict=True)
	return bool(rows)


def execute():
	table = "tabAOS Transactional Outbox"
	if not frappe.db.table_exists("AOS Transactional Outbox"):
		return
	indexes = {
		"idx_aos_outbox_publish_due": "(`status`, `next_attempt_at`, `creation`)",
		"idx_aos_outbox_lease": "(`status`, `lease_expires_at`)",
		"idx_aos_outbox_job": "(`job_doctype`, `job_name`)",
		"idx_aos_outbox_service_status": "(`service_type`, `status`)",
		"idx_aos_outbox_callback_deadline": "(`status`, `callback_deadline_at`)",
	}
	for name, columns in indexes.items():
		if not _index_exists(table, name):
			frappe.db.sql(f"ALTER TABLE `{table}` ADD INDEX `{name}` {columns}")
