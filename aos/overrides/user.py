import frappe
from frappe import _
from frappe.utils.html_utils import escape_html
from frappe.utils import random_string
from frappe.website.utils import is_signup_disabled

@frappe.whitelist(allow_guest=True)
def sign_up(email: str, full_name: str, user_type: str, phone: str, password: str, redirect_to: str) -> tuple[int, str]:
	if is_signup_disabled():
		frappe.throw(_("Sign Up is disabled"), title=_("Not Allowed"))

	user = frappe.db.get("User", {"email": email})
	if user:
		if user.enabled:
			return 0, _("Already Registered")
		else:
			return 0, _("Registered but disabled")
	else:
		if frappe.db.get_creation_count("User", 60) > 300:
			frappe.respond_as_web_page(
				_("Temporarily Disabled"),
				_("Too many users signed up recently, so the registration is disabled. Please try back in an hour"),
				http_status_code=429,
			)

		# Set role profile if vendor
		role_profile = None
		if user_type.lower() == "vendor":
			role_profile = "Vendor"

		# Create user
		user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": escape_html(full_name),
			"enabled": 1,
			"new_password": password or random_string(10),
			"user_type": "System User" if user_type.lower() == "vendor" else "Website User",
			"phone": phone,
			"role_profile_name": role_profile,
			"send_welcome_email": 0
		})

		user.flags.ignore_permissions = True
		user.flags.ignore_password_policy = True
		user.insert()

		# Add Customer role if buyer
		if user_type.lower() == "buyer":
			user.add_roles("Customer")

		# Create linked party if vendor only
		if user_type.lower() == "vendor":
			supplier = frappe.get_doc({
				"doctype": "Supplier",
				"supplier_name": full_name,
				"supplier_type": "Company",
				"custom_vendor": user.name,
				"email_id": email,
				"phone": phone
			})
			supplier.insert(ignore_permissions=True)

			# Add User Permission
			frappe.get_doc({
				"doctype": "User Permission",
				"user": user.name,
				"allow": "Supplier",
				"for_value": supplier.name,
				"apply_to_all_doctypes": 1
			}).insert(ignore_permissions=True)

		if redirect_to:
			frappe.cache.hset("redirect_after_login", user.name, redirect_to)
		else:
			login_url = "/login"
			frappe.msgprint(_("Click <a href='{0}'>here</a> to log in.").format(login_url))
			return 2, _("Registration successful")
