// Copyright (c) 2025, Kalutu Daniel and contributors
// For license information, please see license.txt

frappe.ui.form.on("Product", {
	refresh(frm) {
		frm.set_query("category", () => {
			return {
				filters: {
					is_group: 0,
				},
			};
		});
	},
});
