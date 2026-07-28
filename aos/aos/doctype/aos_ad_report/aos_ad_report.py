from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from aos.services.ads.indexing import enqueue_discovery_refresh
from aos.services.ads.lifecycle import validate_status_transition
from aos.services.ads.mutations import apply_transition, lock_ad
from aos.services.sellers.policy import set_seller_status

_MODERATOR_ROLES = frozenset({"System Manager", "AOS Moderator"})


class AOSAdReport(Document):
    def before_insert(self):
        user = str(getattr(frappe.session, "user", "") or "")
        if user not in {"", "Guest", "Administrator"}:
            self.reported_by = user
        ad = frappe.db.get_value("AOS Ad", self.ad, ["seller", "status"], as_dict=True)
        if not ad or ad.status != "Active":
            frappe.throw("Ad not found.", exc=frappe.DoesNotExistError)
        self.seller = ad.seller

    def validate(self):
        self.details = str(self.details or "").strip()[:2000]
        if not frappe.db.exists("AOS Report Reason", {"name": self.reason, "is_active": 1}):
            frappe.throw("Invalid report reason.", exc=frappe.ValidationError)
        self._validate_duplicate()
        self._validate_moderator_change()
        if self.admin_action and self.status != "Resolved":
            frappe.throw("Admin action requires a resolved report.", exc=frappe.ValidationError)

    def before_save(self):
        previous = self.get_doc_before_save()
        if previous and (previous.status != self.status or previous.admin_action != self.admin_action):
            self.reviewed_by = frappe.session.user
            self.reviewed_on = now_datetime()

    def after_insert(self):
        self._recompute_ad_total_reports()

    def on_update(self):
        self._apply_admin_action_once()
        self._recompute_if_status_changed()

    def on_trash(self):
        self._recompute_ad_total_reports()

    def _validate_duplicate(self):
        if not self.ad or not self.reported_by:
            return
        if frappe.db.exists(
            "AOS Ad Report",
            {"ad": self.ad, "reported_by": self.reported_by, "name": ["!=", self.name]},
        ):
            frappe.throw("You have already reported this ad.", exc=frappe.ValidationError)

    def _validate_moderator_change(self):
        if self.is_new():
            return
        previous = self.get_doc_before_save()
        if not previous or (previous.status == self.status and previous.admin_action == self.admin_action):
            return
        roles = set(frappe.get_roles(frappe.session.user))
        if not roles.intersection(_MODERATOR_ROLES):
            frappe.throw("Moderator permission is required.", exc=frappe.PermissionError)

    def _apply_admin_action_once(self):
        if self.status != "Resolved" or not self.admin_action:
            return
        previous = self.get_doc_before_save()
        if previous and previous.admin_action == self.admin_action and previous.status == self.status:
            return
        if self.admin_action == "Suspended Ad":
            lock_ad(self.ad)
            ad = frappe.get_doc("AOS Ad", self.ad)
            if ad.status != "Suspended":
                transition = validate_status_transition(ad.status, "Suspended", action="suspend")
                apply_transition(ad, transition)
                ad.save(ignore_permissions=True)
                enqueue_discovery_refresh(ad.name, status=ad.status, source="ad_report_suspend")
        elif self.admin_action == "Suspended Seller":
            set_seller_status(
                self.seller,
                status="Suspended",
                reason_code="AD_REPORT_MODERATION",
                source="ad_report",
                actor=str(frappe.session.user or ""),
            )
            for ad_id in frappe.get_all(
                "AOS Ad",
                filters={"seller": self.seller, "status": "Active"},
                pluck="name",
                order_by="name asc",
                limit=500,
            ):
                enqueue_discovery_refresh(ad_id, status="Suspended", source="seller_report_suspend")

    def _recompute_ad_total_reports(self):
        if not self.ad:
            return
        total = frappe.db.count("AOS Ad Report", {"ad": self.ad, "status": ["!=", "Rejected"]})
        frappe.db.set_value("AOS Ad", self.ad, "total_reports", int(total or 0), update_modified=False)

    def _recompute_if_status_changed(self):
        previous = self.get_doc_before_save()
        if not previous or previous.status != self.status:
            self._recompute_ad_total_reports()
