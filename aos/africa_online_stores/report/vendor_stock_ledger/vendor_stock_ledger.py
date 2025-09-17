# Copyright (c) 2025, Kalutu Daniel and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters: dict | None = None):
	user = frappe.session.user

	# Find the Supplier linked to the logged-in user
	supplier = frappe.db.get_value("Supplier", {"custom_vendor": user}, "name")

	if not supplier:
		frappe.throw(_("You're not linked to a vendor account."))

	if not filters:
		filters = {}

	filters = frappe._dict(filters)
	filters["custom_vendor"] = supplier

	columns = get_columns()
	data = get_data(filters)

	return columns, data


def get_columns() -> list[dict]:
	return [
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date"},
		{"label": _("Item"), "fieldname": "item_name", "fieldtype": "Data"},
		{"label": _("Category"), "fieldname": "item_group", "fieldtype": "Data"},
		{"label": _("Qty In"), "fieldname": "qty_in", "fieldtype": "Float"},
		{"label": _("Qty Out"), "fieldname": "qty_out", "fieldtype": "Float"},
		{"label": _("Balance Qty"), "fieldname": "qty_after_transaction", "fieldtype": "Float"},
		{"label": _("Valuation Rate"), "fieldname": "valuation_rate", "fieldtype": "Currency"},
		{"label": _("Balance Value"), "fieldname": "balance_value", "fieldtype": "Currency"},
	]


def get_data(filters: dict) -> list[dict]:
	conditions = ["sle.custom_vendor = %(custom_vendor)s", "sle.is_cancelled = 0"]
	values = {"custom_vendor": filters["custom_vendor"]}

	if filters.get("from_date"):
		conditions.append("sle.posting_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("sle.posting_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if filters.get("item_name"):
		conditions.append("i.item_name LIKE %(item_name)s")
		values["item_name"] = f"%{filters['item_name']}%"
	if filters.get("item_group"):
		conditions.append("i.item_group LIKE %(item_group)s")
		values["item_group"] = f"%{filters['item_group']}%"

	condition_sql = " AND ".join(conditions)

	query = f"""
		SELECT
			sle.posting_date,
			i.item_name,
			i.item_group,
			sle.actual_qty,
			sle.qty_after_transaction,
			sle.valuation_rate
		FROM
			`tabStock Ledger Entry` sle
		LEFT JOIN
			`tabItem` i ON sle.item_code = i.name
		WHERE
			{condition_sql}
		ORDER BY
			sle.posting_date ASC, sle.posting_time ASC
	"""

	raw_data = frappe.db.sql(query, values, as_dict=True)

	for row in raw_data:
		row["qty_in"] = row["actual_qty"] if row["actual_qty"] > 0 else 0
		row["qty_out"] = row["actual_qty"] if row["actual_qty"] < 0 else 0
		row["balance_value"] = row["qty_after_transaction"] * (row["valuation_rate"] or 0)
		del row["actual_qty"]

	return raw_data
