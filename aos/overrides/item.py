import frappe

@frappe.whitelist(allow_guest=True)
def get_supplier_details(vendor_name):
    supplier = frappe.get_doc("Supplier", vendor_name)
    details = {}

    if supplier.supplier_name:
        details['name'] = supplier.supplier_name
    if supplier.email_id:
        details['email'] = supplier.email_id

    # If there is a primary contact, get their phone number
    if supplier.supplier_primary_contact:
        contact = frappe.get_doc("Contact", supplier.supplier_primary_contact)
        details['phone'] = contact.phone or contact.mobile_no
    
    return details
