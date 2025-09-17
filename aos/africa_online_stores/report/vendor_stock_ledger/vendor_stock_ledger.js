// Copyright (c) 2025, Kalutu Daniel and contributors
// For license information, please see license.txt

frappe.query_reports["Vendor Stock Ledger"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 0,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 0,
		},
		{
			fieldname: "item_name",
			label: __("Item"),
			fieldtype: "Data",
			reqd: 0,
		},
		{
			fieldname: "item_group",
			label: __("Category"),
			fieldtype: "Data",
			reqd: 0,
		},
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);

		if (column.fieldname === "qty_in" && data?.qty_in > 0) {
			value = `<span style='color:green;'>${data.qty_in.toFixed(3)}</span>`;
		}
		if (column.fieldname === "qty_out" && data?.qty_out < 0) {
			value = `<span style='color:red;'>${data.qty_out.toFixed(3)}</span>`;
		}
		return value;
	},
};
