// Copyright (c) 2025, Kalutu Daniel and contributors
// For license information, please see license.txt

frappe.query_reports["Vendor Sales Summary"] = {
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
		{
			fieldname: "customer_name",
			label: __("Customer"),
			fieldtype: "Data",
			reqd: 0,
		},
	],
};
