"""Idempotent moderation effects for resolved reports.

Warnings and dismissals are audit-only because the repository does not model a
warning-notification contract. No new notification types are invented here.
"""

from __future__ import annotations

import frappe

from aos.api.auth.session_control import revoke_account_access
from aos.services.accounts.constants import ACCOUNT_STATUS_DELETED, ACCOUNT_STATUS_SUSPENDED
from aos.services.ads.indexing import enqueue_discovery_refresh
from aos.services.ads.lifecycle import validate_status_transition
from aos.services.ads.mutations import apply_transition, lock_ad
from aos.services.sellers.policy import set_seller_status

from .constants import STATUS_RESOLVED
from .observability import report_log


def apply_admin_action_once(doc, previous) -> None:
    action = str(getattr(doc, "admin_action", "") or "").strip()
    if str(getattr(doc, "status", "") or "").strip() != STATUS_RESOLVED or not action:
        return
    if previous and str(getattr(previous, "admin_action", "") or "").strip() == action and str(getattr(previous, "status", "") or "").strip() == STATUS_RESOLVED:
        return

    if doc.doctype == "AOS User Report":
        _apply_user_action(doc, action)
    elif doc.doctype == "AOS Ad Report":
        _apply_ad_action(doc, action)
    elif doc.doctype == "AOS Short Report":
        _apply_short_action(doc, action)

    report_log(
        "report.moderation.applied",
        report_id=doc.name,
        doctype=doc.doctype,
        status=doc.status,
        action=action,
    )


def _apply_user_action(doc, action: str) -> None:
    if action == "Suspend User":
        _suspend_account(str(doc.reported_user or ""))
    # Warn User and Dismiss Report are intentionally audit-only.


def _apply_ad_action(doc, action: str) -> None:
    if action == "Suspended Ad":
        lock_ad(doc.ad)
        ad = frappe.get_doc("AOS Ad", doc.ad)
        if ad.status != "Suspended" and ad.status not in {"Deleted", "Sold", "Expired"}:
            transition = validate_status_transition(ad.status, "Suspended", action="suspend")
            apply_transition(ad, transition)
            ad.save(ignore_permissions=True)
            enqueue_discovery_refresh(ad.name, status=ad.status, source="ad_report_suspend")
    elif action == "Suspended Seller":
        seller_rows = frappe.db.sql(
            "SELECT name, status FROM `tabAOS Seller` WHERE name = %s LIMIT 1 FOR UPDATE",
            (doc.seller,),
            as_dict=True,
        )
        if not seller_rows or str(seller_rows[0].status or "") == "Deleted":
            return
        set_seller_status(
            doc.seller,
            status="Suspended",
            reason_code="AD_REPORT_MODERATION",
            source="ad_report",
            actor=str(getattr(frappe.session, "user", "") or ""),
        )
        for ad_id in frappe.get_all(
            "AOS Ad",
            filters={"seller": doc.seller, "status": "Active"},
            pluck="name",
            order_by="name asc",
            limit=500,
        ):
            enqueue_discovery_refresh(ad_id, status="Suspended", source="seller_report_suspend")
    # Warn Seller is intentionally audit-only.


def _apply_short_action(doc, action: str) -> None:
    if action == "Hide Short":
        rows = frappe.db.sql(
            "SELECT name FROM `tabAOS Short` WHERE name = %s FOR UPDATE",
            (doc.short,),
            as_dict=True,
        )
        if not rows:
            return
        short = frappe.get_doc("AOS Short", doc.short)
        if str(short.status or "") == "deleted" or str(short.visibility_status or "") == "deleted":
            return
        short.visibility_status = "hidden"
        if hasattr(short, "approval_status"):
            short.approval_status = "flagged"
        if hasattr(short, "hidden_reason"):
            short.hidden_reason = "Hidden after report review."
        short.save(ignore_permissions=True)
        try:
            from aos.services.search_ranking_service import enqueue_short_search_index

            enqueue_short_search_index(short.name, source="short_report_hide")
        except Exception:
            frappe.log_error(frappe.get_traceback(), "AOS Short Report search refresh failed")
    elif action == "Suspend Creator":
        _suspend_account(str(doc.short_owner or ""))
    # Warn Creator and Dismiss Report are intentionally audit-only.


def _suspend_account(user: str) -> None:
    user = str(user or "").strip()
    if not user:
        return
    rows = frappe.db.sql(
        "SELECT name, account_status, is_deleted FROM `tabAOS Profile` WHERE user = %s LIMIT 1 FOR UPDATE",
        (user,),
        as_dict=True,
    )
    if not rows:
        return
    profile = rows[0]
    # Never resurrect or downgrade a deleted account while resolving an older report.
    if str(profile.account_status or "") == ACCOUNT_STATUS_DELETED or int(profile.is_deleted or 0):
        return
    frappe.db.set_value(
        "AOS Profile",
        profile.name,
        {"account_status": ACCOUNT_STATUS_SUSPENDED},
        update_modified=True,
    )
    if frappe.db.exists("User", user):
        frappe.db.set_value("User", user, "enabled", 0, update_modified=True)
        revoke_account_access(user)
