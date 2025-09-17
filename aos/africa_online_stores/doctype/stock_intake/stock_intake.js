// Copyright (c) 2025, Kalutu Daniel and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stock Intake", {
	onload(frm) {
		// Prevent selecting duplicate items
		frm.fields_dict.items.grid.get_field("item").get_query = function (doc, cdt, cdn) {
			let current_row = locals[cdt][cdn];
			let selected_items = (doc.items || [])
				.filter((row) => row.name !== current_row.name && row.item)
				.map((row) => row.item);

			return {
				filters: {
					name: ["not in", selected_items],
				},
			};
		};
	},
});

frappe.ui.form.on("Stock Intake Item", {
	qty(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.qty <= 0) {
			frappe.model.set_value(cdt, cdn, "qty", "");
			frappe.msgprint({
				title: "Invalid Quantity",
				message: "Quantity must be greater than 0.",
			});
		}
	},

	item(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		let is_duplicate = (frm.doc.items || []).some(
			(r) => r.name !== row.name && r.item === row.item
		);

		if (is_duplicate) {
			frappe.msgprint({
				title: "Duplicate Item",
				message: `Item <b>${row.item}</b> has already been added.`,
				indicator: "red",
			});
			frappe.model.set_value(cdt, cdn, "item", null);
		}
	},
});
