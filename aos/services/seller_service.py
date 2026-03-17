import frappe


def get_or_create_seller(user: str):
    """
    Ensure a seller record exists for the given user.
    Returns the seller document.
    """

    # Since autoname = field:user, the docname is the user
    if frappe.db.exists("AOS Seller", user):
        return frappe.get_doc("AOS Seller", user)

    # Fetch user full name
    full_name = frappe.db.get_value("User", user, "full_name") or user

    seller = frappe.get_doc({
        "doctype": "AOS Seller",
        "user": user,
        "shop_name": full_name,
        "status": "Active"
    })

    seller.insert(ignore_permissions=True)

    return seller
