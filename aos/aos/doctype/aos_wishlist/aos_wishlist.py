from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document


def wishlist_name(user: str, ad: str) -> str:
    digest = hashlib.sha256(f"{user}\0{ad}".encode("utf-8")).hexdigest()[:32]
    return f"WISH-{digest}"


class AOSWishlist(Document):
    def autoname(self):
        if not self.user or not self.ad:
            frappe.throw("User and Ad are required.", exc=frappe.ValidationError)
        self.name = wishlist_name(str(self.user), str(self.ad))

    def validate(self):
        if self.status not in {"Active", "Removed"}:
            frappe.throw("Invalid wishlist status.", exc=frappe.ValidationError)
        if not self.is_new():
            previous = self.get_doc_before_save()
            if previous and (previous.user != self.user or previous.ad != self.ad):
                frappe.throw("Wishlist ownership cannot be changed.", exc=frappe.ValidationError)
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"} and "System Manager" not in frappe.get_roles(user):
            if user != self.user:
                frappe.throw("Not permitted.", exc=frappe.PermissionError)
