import frappe

def run_all_setup():
    set_stock_settings()
    set_website_settings()
    set_webshop_settings()

def set_stock_settings():
    settings = frappe.get_single("Stock Settings")
    settings.item_naming_by = "Naming Series"
    settings.save()

def set_website_settings():
    settings = frappe.get_single("Website Settings")
    settings.home_page = "all-products"
    settings.title_prefix = "AOS"
    settings.app_name = "Africa Online Stores®"
    settings.app_logo = "/files/aos-logo-sm.png"
    settings.disable_signup = 0
    settings.banner_image = "/files/aos-logo-sm.png"
    settings.splash_image = "/files/aos-logo-sm.png"
    settings.favicon = "/files/aos-logo-sm.png"
    settings.copyright = "2025 Africa Online Stores"
    settings.footer_powered = "Powered by Africa Online Stores"
    settings.save()

def set_webshop_settings():
    settings = frappe.get_single("Webshop Settings")

    settings.products_per_page = 12
    settings.enable_field_filters = 1
    settings.enable_attribute_filters = 1
    settings.enable_variants = 1
    settings.show_price = 1
    settings.show_stock_availability = 1
    settings.show_contact_us_button = 1
    settings.show_attachments = 1
    settings.enabled = 1
    settings.enable_checkout = 1
    settings.enable_wishlist = 1
    settings.enable_reviews = 1
    settings.enable_recommendations = 1

    company = frappe.db.get_default("company")
    if not company:
        frappe.throw("Default company is not set. Please set it in Global Defaults before running setup.")

    settings.company = company
    settings.price_list = frappe.db.get_default("selling_price_list") or "Standard Selling"
    settings.default_customer_group = "Individual"
    settings.quotation_series = "SAL-QTN-.YYYY.-"
    settings.payment_success_url = "Orders"

    # Set Website Filter Fields
    settings.set("filter_fields", [])
    settings.append("filter_fields", {
        "fieldname": "item_group"
    })

    # Set Website Attributes from all available Item Attributes
    settings.set("filter_attributes", [])
    all_attributes = frappe.get_all("Item Attribute", pluck="name")
    for attr in all_attributes:
        settings.append("filter_attributes", {
            "attribute": attr
        })

    # Ensure Payment Gateway "Cash" exists
    if not frappe.db.exists("Payment Gateway", "Cash"):
        frappe.get_doc({
            "doctype": "Payment Gateway",
            "gateway": "Cash"
        }).insert(ignore_permissions=True)

    # Create Account if missing
    abbr = frappe.db.get_value("Company", company, "abbr")
    if not abbr:
        frappe.throw(f"Company abbreviation not found for {company}.")

    account_name = f"Cash - {abbr}"
    if not frappe.db.exists("Account", account_name):
        root_account = frappe.db.get_value("Account", {
            "company": company,
            "is_group": 1,
            "root_type": "Asset"
        }, "name")
        if not root_account:
            frappe.throw(f"Root Asset account not found for company {company}.")

        frappe.get_doc({
            "doctype": "Account",
            "account_name": "Cash",
            "parent_account": root_account,
            "company": company,
            "is_group": 0,
            "account_type": "Cash"
        }).insert(ignore_permissions=True)

    # Ensure Payment Gateway Account exists
    if not frappe.db.exists("Payment Gateway Account", {"payment_gateway": "Cash"}):
        pga = frappe.get_doc({
            "doctype": "Payment Gateway Account",
            "payment_gateway": "Cash",
            "title": "Cash Payment - KES",
            "mode_of_payment": "Cash",
            "payment_account": account_name,
            "is_default": 1
        })
        pga.insert(ignore_permissions=True)
        gateway_account = pga.name
    else:
        gateway_account = frappe.db.get_value("Payment Gateway Account", {"payment_gateway": "Cash"}, "name")

    settings.payment_gateway_account = gateway_account
    settings.save()
