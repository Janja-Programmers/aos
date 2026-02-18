import frappe


def get_or_create_seller(user: str):
    """
    Ensures seller record exists for user.
    Returns seller doc.
    """

    if frappe.db.exists("AOS Seller", user):
        return frappe.get_doc("AOS Seller", user)

    # Create seller automatically
    full_name = frappe.db.get_value("User", user, "full_name")

    seller = frappe.get_doc({
        "doctype": "AOS Seller",
        "user": user,
        "shop_name": full_name,
        "status": "Active"
    })

    seller.insert(ignore_permissions=True)

    return seller
