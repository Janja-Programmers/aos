# Copyright (c) 2025, Kalutu Daniel and contributors
# For license information, please see license.txt

from datetime import datetime

import frappe
from frappe import _
from frappe.contacts.doctype.contact.contact import get_contact_name
from frappe.model.document import Document

class DuplicateReport(frappe.ValidationError):
    pass

class ReportedProduct(Document):
	pass

@frappe.whitelist()
def report_product(web_item, reason, comment=None):
    """Add a Reported Product by a user if non-existent."""
    if frappe.session.user == "Guest":
        frappe.throw(_("You are not verified to report a product yet."), exc=frappe.PermissionError)

    # Prevent duplicate reports
    if not frappe.db.exists("Reported Product", {"user": frappe.session.user, "website_item": web_item}):
        doc = frappe.new_doc("Reported Product")
        doc.update({
            "user": frappe.session.user,
            "customer": get_customer(),
            "website_item": web_item,
            "item": frappe.db.get_value("Website Item", web_item, "item_code"),
            "reason": reason,
            "comment": comment,
            "published_on": datetime.today().strftime("%d %B %Y"),
            "status": "Pending"
        })
        doc.insert()
        return {"message": _("Report submitted successfully")}
    else:
        frappe.throw(_("You have already reported this product."), exc=DuplicateReport)

def get_customer(silent=False):
    """
    Get the Customer linked to the logged-in user.
    silent: if True, return None instead of throwing an error.
    """
    user = frappe.session.user
    contact_name = get_contact_name(user)
    customer = None

    if contact_name:
        contact = frappe.get_doc("Contact", contact_name)
        for link in contact.links:
            if link.link_doctype == "Customer":
                customer = link.link_name
                break

    if customer:
        return frappe.db.get_value("Customer", customer)
    elif silent:
        return None
    else:
        frappe.throw(
            _("You are not a verified customer yet. Please contact us to proceed."),
            exc=frappe.PermissionError
        )
