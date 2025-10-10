frappe.ready(() => {
	frappe.call({
		method: "frappe.client.get_value",
		args: {
			doctype: "Global Defaults",
			fieldname: ["default_company"],
		},
		callback: function (r) {
			if (r.message && r.message.default_company) {
				const companyName = r.message.default_company;

				const navbarBrand = document.querySelector(".navbar-brand.navbar-home");
				if (navbarBrand) {
					const span = document.createElement("span");
					span.classList.add("navbar-company-name");
					span.textContent = ` ${companyName}`;
					navbarBrand.appendChild(span);
				}
			}
		},
	});
});
