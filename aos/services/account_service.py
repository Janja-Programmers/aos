import frappe


def get_or_create_seller(user: str):
    if not user or user == "Guest":
        return None

    if frappe.db.exists("AOS Seller", user):
        return frappe.get_doc("AOS Seller", user)

    seller = frappe.get_doc({
        "doctype": "AOS Seller",
        "user": user,
        "status": "Active",
    })

    seller.insert(ignore_permissions=True)

    return seller
