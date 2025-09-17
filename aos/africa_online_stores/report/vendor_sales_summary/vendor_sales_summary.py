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

	filters["custom_vendor"] = supplier

	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)

	return columns, data, None, chart

def get_columns() -> list[dict]:
	return [
		{"label": _("Date"), "fieldname": "transaction_date", "fieldtype": "Date"},
		{"label": _("Customer"), "fieldname": "customer_name", "fieldtype": "Data"},
		{"label": _("Item"), "fieldname": "item_name", "fieldtype": "Data"},
		{"label": _("Category"), "fieldname": "item_group", "fieldtype": "Data"},
		{"label": _("Quantity"), "fieldname": "qty", "fieldtype": "Float"},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency"},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency"},
		{"label": _("Billed Amount"), "fieldname": "billed_amt", "fieldtype": "Currency"},
		{"label": _("Delivered Qty"), "fieldname": "delivered_qty", "fieldtype": "Float"},
	]

def get_data(filters: dict) -> list[dict]:
	conditions = ""

	if filters.get("from_date"):
		conditions += " AND so.transaction_date >= %(from_date)s"
	if filters.get("to_date"):
		conditions += " AND so.transaction_date <= %(to_date)s"
	if filters.get("item_name"):
		conditions += " AND soi.item_name LIKE %(item_name)s"
		filters["item_name"] = f"%{filters['item_name']}%"
	if filters.get("item_group"):
		conditions += " AND i.item_group LIKE %(item_group)s"
		filters["item_group"] = f"%{filters['item_group']}%"
	if filters.get("customer_name"):
		conditions += " AND so.customer_name LIKE %(customer_name)s"
		filters["customer_name"] = f"%{filters['customer_name']}%"

	return frappe.db.sql(f"""
		SELECT
			so.transaction_date,
			so.customer_name,
			soi.item_name,
			i.item_group,
			soi.qty,
			soi.rate,
			soi.amount,
			soi.billed_amt,
			soi.delivered_qty
		FROM
			`tabSales Order` so
		JOIN
			`tabSales Order Item` soi ON so.name = soi.parent
		LEFT JOIN
			`tabItem` i ON soi.item_code = i.name
		WHERE
			so.custom_vendor = %(custom_vendor)s
			AND so.docstatus = 1
			{conditions}
		ORDER BY
			so.transaction_date DESC
	""", values=filters, as_dict=True)

def get_chart(data: list[dict]) -> dict:
	item_totals = {}

	# Aggregate total amount per item
	for row in data:
		item = row["item_name"]
		amount = row.get("amount", 0) or 0
		item_totals[item] = item_totals.get(item, 0) + amount

	# Sort by amount descending (optional)
	sorted_items = sorted(item_totals.items(), key=lambda x: x[1], reverse=True)

	labels = [item for item, _ in sorted_items]
	values = [total for _, total in sorted_items]

	return {
		"data": {
			"labels": labels,
			"datasets": [
				{
					"name": "Sales Amount",
					"values": values
				}
			]
		},
		"type": "bar",
		"colors": ["#7cd6fd"]
	}
