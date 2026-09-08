"""Application service for the existing User, Ad, and Short report APIs."""

from __future__ import annotations

import hashlib
from typing import Any

import frappe
from frappe.utils import getdate, nowdate

from aos.api.ads.activity import record_ad_report_activity
from aos.api.shared.db import is_duplicate_entry_error
from aos.api.shorts.activity import record_short_report_activity
from aos.api.shorts.visibility import can_view_short
from aos.api.social.activity import record_block_user_activity, record_report_user_activity
from aos.services.accounts.identity import resolve_account_reference
from aos.services.social.service import SocialService

from .constants import (
    AD_REPORT_FIELDS,
    SHORT_REPORT_FIELDS,
    STATUS_REVIEWING,
    USER_REPORT_FIELDS,
)
from .errors import ReportConflictError, ReportNotFoundError, ReportPermissionError, ReportValidationError
from .observability import report_log
from .policy import require_reportable_user
from .validation import (
    aliased_value,
    ensure_known_fields,
    normalize_details,
    normalize_reason,
    strip_transport_fields,
    normalize_optional_bool,
    validate_active_reason,
)


def user_active_key(reported_user: str, reported_by: str) -> str:
    material = f"{str(reported_user or '').strip()}\x1f{str(reported_by or '').strip()}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


class ReportService:
    """Report use-cases; caller owns commit/rollback."""

    def report_user(
        self,
        *,
        user: str,
        payload: dict[str, Any],
        activity_callback=record_report_user_activity,
        block_activity_callback=record_block_user_activity,
    ) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, USER_REPORT_FIELDS)
        target_user = resolve_account_reference(aliased_value(request, "target_user", "user")) or ""
        require_reportable_user(target_user=target_user, reporter=user)
        reason = validate_active_reason(normalize_reason(request.get("reason")))
        details = normalize_details(request.get("details"), max_length=1000)
        block_value = aliased_value(request, "block_user", "also_block")
        should_block = normalize_optional_bool(block_value, field="block_user")

        # Serialize the pair through the target profile. The unique active_key is
        # the final integrity boundary after migration.
        locked_profiles = frappe.db.sql(
            "SELECT name FROM `tabAOS Profile` WHERE user = %s LIMIT 1 FOR UPDATE",
            (target_user,),
            as_dict=True,
        )
        # Revalidate after waiting for the account lock so a concurrent
        # suspension/deletion cannot cross the report-submission boundary.
        require_reportable_user(target_user=target_user, reporter=user)
        existing = frappe.db.get_value(
            "AOS User Report",
            {"reported_user": target_user, "reported_by": user, "status": ["!=", "Rejected"]},
            "name",
        )
        if existing:
            raise ReportConflictError("You have already reported this user.", code="VALIDATION_ERROR", http_status=422)

        doc = frappe.new_doc("AOS User Report")
        doc.reported_user = target_user
        doc.reported_by = user
        doc.reason = reason
        doc.details = details
        doc.status = STATUS_REVIEWING
        if hasattr(doc, "active_key"):
            doc.active_key = user_active_key(target_user, user)
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            raise ReportConflictError("You have already reported this user.", code="VALIDATION_ERROR", http_status=422) from exc

        activity_callback(user=user, target_user=target_user, report_id=doc.name, reason=reason)
        response = {
            "id": doc.name,
            "block_requested": should_block,
            "block_applied": False,
            "block_status": None,
        }
        if should_block:
            savepoint = f"report_block_{hashlib.sha1(doc.name.encode()).hexdigest()[:10]}"
            frappe.db.savepoint(savepoint)
            try:
                target_account_id = str(locked_profiles[0].name) if locked_profiles else ""
                block_data = SocialService().block(
                    actor=user,
                    payload={"account_id": target_account_id, "reason": f"Reported user: {reason}"},
                    activity_callback=block_activity_callback,
                )
                response["block_status"] = block_data
                response["block_applied"] = True
            except Exception:
                # Blocking is explicitly optional. Roll back only its nested work;
                # the report remains part of the caller's transaction.
                frappe.db.rollback(save_point=savepoint)
                response["block_error"] = {
                    "message": "The report was submitted, but the user could not be blocked.",
                    "error": "BLOCK_FAILED",
                }

        report_log("report.submitted", report_id=doc.name, doctype=doc.doctype, status=doc.status)
        return response

    def report_short(self, *, user: str, payload: dict[str, Any], activity_callback=record_short_report_activity) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, SHORT_REPORT_FIELDS)
        short_id = str(aliased_value(request, "short_id", "short") or "").strip()
        if not short_id or len(short_id) > 140:
            raise ReportValidationError("Short id is required.")
        reason = validate_active_reason(normalize_reason(request.get("reason")))
        details = normalize_details(request.get("details"), max_length=1000)
        locked = frappe.db.sql(
            "SELECT name FROM `tabAOS Short` WHERE name = %s FOR UPDATE",
            (short_id,),
        )
        if not locked:
            raise ReportNotFoundError("Short not found.")
        short = frappe.db.get_value(
            "AOS Short",
            short_id,
            ["name", "owner", "status", "visibility_status", "audience"],
            as_dict=True,
        )
        if not short or short.status != "ready" or short.visibility_status != "visible" or not can_view_short(short, current_user=user):
            raise ReportNotFoundError("Short not found.")
        if short.owner == user:
            raise ReportPermissionError("You cannot report your own short.", code="VALIDATION_ERROR", http_status=422)

        # The existing uq_short_report_active index remains the final DB guard.
        if frappe.db.exists(
            "AOS Short Report", {"short": short_id, "reported_by": user, "status": ["!=", "Rejected"]}
        ):
            raise ReportConflictError("You have already reported this short.", code="VALIDATION_ERROR", http_status=422)
        doc = frappe.new_doc("AOS Short Report")
        doc.short = short_id
        doc.short_owner = short.owner
        doc.reported_by = user
        doc.reason = reason
        doc.details = details
        doc.status = STATUS_REVIEWING
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            raise ReportConflictError("You have already reported this short.", code="VALIDATION_ERROR", http_status=422) from exc
        activity_callback(user=user, short_id=short_id, report_id=doc.name, reason=reason)
        report_log("report.submitted", report_id=doc.name, doctype=doc.doctype, status=doc.status)
        return {"id": doc.name, "short_id": short_id}

    def report_ad(self, *, user: str, payload: dict[str, Any], activity_callback=record_ad_report_activity) -> dict[str, Any]:
        request = strip_transport_fields(payload)
        ensure_known_fields(request, AD_REPORT_FIELDS)
        ad_id = str(aliased_value(request, "ad_id", "ad") or "").strip()
        if not ad_id or len(ad_id) > 140:
            raise ReportValidationError("Ad id is required.")
        reason = validate_active_reason(normalize_reason(request.get("reason")))
        details = normalize_details(request.get("details"), max_length=2000)
        locked = frappe.db.sql(
            "SELECT name FROM `tabAOS Ad` WHERE name = %s FOR UPDATE",
            (ad_id,),
        )
        if not locked:
            raise ReportNotFoundError("Ad not found.", code="AD_NOT_FOUND")
        ad = frappe.db.get_value("AOS Ad", ad_id, ["name", "seller", "status", "expires_on"], as_dict=True)
        if not ad or ad.status != "Active" or (ad.expires_on and getdate(ad.expires_on) < getdate(nowdate())):
            raise ReportNotFoundError("Ad not found.", code="AD_NOT_FOUND")
        frappe.db.sql("SELECT name FROM `tabAOS Seller` WHERE name = %s FOR UPDATE", (ad.seller,))
        seller = frappe.db.get_value("AOS Seller", ad.seller, ["user", "status"], as_dict=True)
        if not seller or seller.status != "Active":
            raise ReportNotFoundError("Ad not found.", code="AD_NOT_FOUND")
        if seller.user == user:
            raise ReportPermissionError("You cannot report your own ad.", code="INVALID_AD_INPUT", http_status=422)

        existing = frappe.db.sql(
            """
            SELECT name FROM `tabAOS Ad Report`
            WHERE ad = %s AND reported_by = %s
            ORDER BY creation ASC, name ASC
            LIMIT 1 FOR UPDATE
            """,
            (ad_id, user),
        )
        if existing:
            raise ReportConflictError("You have already reported this ad.", code="DUPLICATE", http_status=409)
        doc = frappe.get_doc(
            {
                "doctype": "AOS Ad Report",
                "ad": ad_id,
                "reason": reason,
                "details": details,
                "reported_by": user,
                "seller": ad.seller,
            }
        )
        try:
            doc.insert(ignore_permissions=True)
        except Exception as exc:
            if not is_duplicate_entry_error(exc):
                raise
            raise ReportConflictError("You have already reported this ad.", code="DUPLICATE", http_status=409) from exc
        activity_callback(user=user, ad_id=ad_id, report_id=doc.name, reason=reason)
        report_log("report.submitted", report_id=doc.name, doctype=doc.doctype, status=doc.status)
        return {"id": doc.name}
