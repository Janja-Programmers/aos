import frappe


def get_or_create_seller(user: str):
    if not user or user == "Guest":
        return None

    if frappe.db.exists("AOS Seller", user):
        return frappe.get_doc("AOS Seller", user)

    full_name = frappe.db.get_value("User", user, "full_name") or user

    seller = frappe.get_doc({
        "doctype": "AOS Seller",
        "user": user,
        "shop_name": full_name,
        "status": "Active",
    })

    seller.insert(ignore_permissions=True)

    return seller


def get_or_create_profile(user: str):
    if not user or user == "Guest":
        return None

    if frappe.db.exists("AOS Profile", user):
        return frappe.get_doc("AOS Profile", user)

    profile = frappe.get_doc({
        "doctype": "AOS Profile",
        "user": user,
    })

    profile.insert(ignore_permissions=True)

    return profile
