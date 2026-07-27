from __future__ import annotations

import hashlib

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.wishlist.constants import (
    WISHLIST_STATUS_ACTIVE,
    WISHLIST_STATUS_REMOVED,
    WISHLIST_STATUSES,
)
from aos.services.wishlist.counters import apply_wishlist_count_delta


def wishlist_name(user: str, ad: str) -> str:
    digest = hashlib.sha256(f"{user}\0{ad}".encode("utf-8")).hexdigest()[:32]
    return f"WISH-{digest}"


class AOSWishlist(Document):
    def autoname(self):
        if not self.user or not self.ad:
            frappe.throw("User and Ad are required.", exc=frappe.ValidationError)
        self.name = wishlist_name(str(self.user), str(self.ad))

    def before_insert(self):
        self.user = str(self.user or "").strip()
        self.ad = str(self.ad or "").strip()
        now = now_datetime()
        if self.status == WISHLIST_STATUS_ACTIVE:
            self.saved_on = self.saved_on or now
            self.removed_on = None
        else:
            self.saved_on = self.saved_on or now
            self.removed_on = self.removed_on or now

    def validate(self):
        if self.status not in WISHLIST_STATUSES:
            frappe.throw("Invalid wishlist status.", exc=frappe.ValidationError)
        if self.user == "Guest":
            frappe.throw("Guest cannot own a wishlist item.", exc=frappe.ValidationError)
        if not self.is_new():
            previous = self.get_doc_before_save()
            if previous and (previous.user != self.user or previous.ad != self.ad):
                frappe.throw("Wishlist ownership cannot be changed.", exc=frappe.ValidationError)

        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"} and "System Manager" not in frappe.get_roles(user):
            if user != self.user:
                frappe.throw("Not permitted.", exc=frappe.PermissionError)

        if self.status == WISHLIST_STATUS_ACTIVE:
            seller_user = frappe.db.get_value(
                "AOS Seller",
                {"name": frappe.db.get_value("AOS Ad", self.ad, "seller")},
                "user",
            )
            if seller_user and seller_user == self.user:
                frappe.throw(
                    "You cannot add your own ad to your wishlist.",
                    exc=frappe.ValidationError,
                )

    def before_save(self):
        if self.is_new():
            return
        previous = self.get_doc_before_save()
        if not previous:
            return

        now = now_datetime()
        if previous.status != self.status:
            if self.status == WISHLIST_STATUS_ACTIVE:
                self.saved_on = now
                self.removed_on = None
            elif self.status == WISHLIST_STATUS_REMOVED:
                self.removed_on = now
        elif self.status == WISHLIST_STATUS_ACTIVE and not self.saved_on:
            self.saved_on = self.creation or now

    def after_insert(self):
        if self.status == WISHLIST_STATUS_ACTIVE:
            apply_wishlist_count_delta(
                self.ad,
                1,
                source="wishlist_insert",
            )

    def on_update(self):
        previous = self.get_doc_before_save()
        if not previous or previous.status == self.status:
            return
        if self.status == WISHLIST_STATUS_ACTIVE:
            apply_wishlist_count_delta(
                self.ad,
                1,
                source="wishlist_restore",
            )
        elif previous.status == WISHLIST_STATUS_ACTIVE:
            apply_wishlist_count_delta(
                self.ad,
                -1,
                source="wishlist_remove",
            )

    def on_trash(self):
        if self.status == WISHLIST_STATUS_ACTIVE:
            apply_wishlist_count_delta(
                self.ad,
                -1,
                source="wishlist_delete",
            )
