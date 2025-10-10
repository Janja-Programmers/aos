frappe.after_ajax(() => {
	let checkNavbar = setInterval(() => {
		const navbar = document.querySelector(".navbar-brand.navbar-home");

		if (navbar) {
			clearInterval(checkNavbar);

			const className = "navbar-company-name";
			if (!document.querySelector("." + className)) {
				const span = document.createElement("span");
				span.className = className;

				frappe.call({
					method: "frappe.client.get_value",
					args: {
						doctype: "Global Defaults",
						fieldname: ["default_company"],
					},
					callback: function (r) {
						span.textContent = r.message?.default_company || "Africa Online Stores";
						navbar.appendChild(span);
					},
				});
			}
		}
	}, 200);
});
