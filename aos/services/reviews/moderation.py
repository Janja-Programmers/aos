"""Review-owned moderation lifecycle integration.

The generic Moderation feature supplies automated classification/job plumbing.
Reviews remains authoritative for its state transitions and Desk actions.
"""

from __future__ import annotations

from typing import Any

import frappe

from aos.services.notifications.service import NotificationService
from aos.utils.doctype_permissions import has_doctype_permission

from .errors import ReviewConflictError, ReviewNotFoundError, ReviewPermissionError, ReviewStateError, ReviewValidationError
from .ids import resolve_review_name
from .validation import normalize_identifier, normalize_moderation_reason, normalize_version


def review_review(
    *,
    review_id: Any,
    decision: Any,
    reason: Any,
    version: Any,
    reviewer: str,
):
    """Apply one permission-protected manual Review decision from Frappe Desk."""

    reviewer = str(reviewer or "").strip()
    if reviewer in {"", "Guest"} or not has_doctype_permission(
        user=reviewer,
        doctype="AOS Review",
        ptype="write",
    ):
        raise ReviewPermissionError("Manual review is not allowed.", code="REVIEW_MODERATION_NOT_ALLOWED")

    public_id = normalize_identifier(review_id, field="review_id")
    clean_decision = str(decision or "").strip().lower()
    if clean_decision not in {"approve", "reject"}:
        raise ReviewValidationError("Invalid review decision.", code="INVALID_REVIEW_DECISION")
    clean_reason = normalize_moderation_reason(reason)
    if clean_decision == "reject" and not clean_reason:
        raise ReviewValidationError("A rejection reason is required.", code="INVALID_REVIEW_DECISION")
    if clean_decision == "approve" and clean_reason:
        raise ReviewValidationError("A rejection reason is only valid when rejecting.", code="INVALID_REVIEW_DECISION")
    expected_version = normalize_version(version)

    name = resolve_review_name(public_id)
    rows = frappe.db.sql(
        "SELECT name FROM `tabAOS Review` WHERE name = %s FOR UPDATE",
        (name,),
    )
    if not rows:
        raise ReviewNotFoundError("Review not found.")
    doc = frappe.get_doc("AOS Review", name)
    if str(doc.modified or "") != expected_version:
        raise ReviewConflictError(
            "Review changed since it was loaded.",
            code="REVIEW_VERSION_CONFLICT",
            data={"review_id": doc.public_id, "version": str(doc.modified or "")},
        )

    previous_status = str(doc.status or "")
    action = "manual_approve" if clean_decision == "approve" else "manual_reject"
    doc.flags.aos_review_action = action
    doc.flags.aos_reviewed_by = reviewer
    doc.status = "Approved" if clean_decision == "approve" else "Rejected"
    doc.review_notes = "" if clean_decision == "approve" else clean_reason
    try:
        doc.save(ignore_permissions=True)
    except frappe.ValidationError as exc:
        raise ReviewStateError("Review cannot make that moderation transition.", code="INVALID_REVIEW_TRANSITION") from exc

    notify_review_decision(doc=doc, previous_status=previous_status, decision=clean_decision)
    return doc


def notify_review_decision(*, doc, previous_status: str, decision: str) -> None:
    """Emit product-intended Review notifications using only public resource IDs."""

    ad = frappe.db.get_value(
        "AOS Ad",
        doc.ad,
        ["public_id", "seller"],
        as_dict=True,
    )
    if not ad or not ad.public_id:
        return
    try:
        if decision == "approve" and previous_status != "Approved":
            NotificationService.notify_review_approved(
                user=doc.reviewer,
                review_id=doc.public_id,
                ad_id=ad.public_id,
            )
            if int(getattr(doc, "edit_count", 0) or 0) == 0:
                seller_user = frappe.db.get_value("AOS Seller", ad.seller, "user")
                if seller_user:
                    NotificationService.notify_review_received(
                        user=seller_user,
                        actor=doc.reviewer,
                        review_id=doc.public_id,
                        ad_id=ad.public_id,
                    )
        elif decision == "reject" and previous_status != "Rejected":
            NotificationService.notify_review_rejected(
                user=doc.reviewer,
                review_id=doc.public_id,
                ad_id=ad.public_id,
            )
    except Exception:
        frappe.log_error(frappe.get_traceback(), "Review moderation notification failed")
