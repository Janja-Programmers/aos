frappe.ready(() => {
	$(".btn-inquiry").click((e) => {
		const vendor = e.currentTarget.getAttribute("data-vendor");

		if (!vendor) {
			frappe.msgprint(__("Vendor information is not available."));
			return;
		}

		frappe.call({
			method: "aos.overrides.item.get_supplier_details",
			args: {
				vendor_name: vendor,
			},
			callback: ({ message: supplier }) => {
				if (!supplier) {
					frappe.msgprint(__("Could not retrieve vendor information."));
					return;
				}

				const details = [];
				if (supplier.name) {
					details.push(`
                        <div class="vendor-field">
                            <span class="vendor-label">👤 Name:</span>
                            <span>${supplier.name}</span>
                        </div>`);
				}
				if (supplier.email) {
					details.push(`
                        <div class="vendor-field">
                            <span class="vendor-label">📧 Email:</span>
                            <span>${supplier.email}</span>
                        </div>`);
				}
				const renderMessage = (phone) => {
					if (phone) {
						details.push(`
                            <div class="vendor-field">
                                <span class="vendor-label">📞 Phone:</span>
                                <span>${phone}</span>
                            </div>`);
					}

					const content = details.length
						? `
                            <div class="vendor-details-box">
                                ${details.join("")}
                            </div>
                            <style>
                                .vendor-details-box {
                                    font-size: 14px;
                                    line-height: 1.6;
                                    padding: 16px;
                                    background: #fff;
                                    border: 1px solid #e0e0e0;
                                    border-radius: 8px;
                                    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
                                    margin-top: 10px;
                                }
                                .vendor-field {
                                    margin-bottom: 10px;
                                }
                                .vendor-label {
                                    font-weight: 600;
                                    color: #444;
                                    min-width: 100px;
                                    display: inline-block;
                                }
                            </style>
                        `
						: __("No vendor contact details are available.");

					frappe.msgprint({
						title: __("Vendor Contact Information"),
						indicator: "blue",
						message: content,
					});
				};

				renderMessage(supplier.phone || null);
			},
		});
	});
});
