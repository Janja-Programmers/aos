"""Authorized manual Ads review actions.

Moderation remains a separate feature. This module only provides the human
review boundary required by the Ads lifecycle and records the actual reviewer.
"""
from __future__ import annotations

import frappe

from aos.services.notifications.service import NotificationService
from aos.utils.doctype_permissions import has_doctype_permission

from .authorization import seller_user
from .concurrency import assert_version
from .errors import AdsNotFoundError, AdsPermissionError, AdsValidationError
from .indexing import enqueue_discovery_refresh
from .lifecycle import validate_status_transition
from .mutations import apply_transition, lock_ad
from .validation import normalize_text
from aos.services.marketplace_discovery.ids import resolve_ad_name


def review_ad(*, public_id: str, decision: str, reason: str, version: str, reviewer: str):
    reviewer = str(reviewer or "").strip()
    if reviewer in {"", "Guest"} or not has_doctype_permission(user=reviewer, doctype="AOS Ad", ptype="write"):
        raise AdsPermissionError("Manual review is not allowed.", code="REVIEW_NOT_ALLOWED")

    clean_decision = normalize_text(decision, field="decision", max_length=20, required=True).lower()
    if clean_decision not in {"approve", "reject"}:
        raise AdsValidationError("Invalid review decision.", code="INVALID_AD_INPUT")
    clean_reason = normalize_text(reason, field="reason", max_length=1000, multiline=True)
    if clean_decision == "reject" and not clean_reason:
        raise AdsValidationError("A rejection reason is required.", code="INVALID_AD_INPUT")

    ad_name = resolve_ad_name(public_id)
    lock_ad(ad_name)
    if not frappe.db.exists("AOS Ad", ad_name):
        raise AdsNotFoundError("Ad not found.")
    doc = frappe.get_doc("AOS Ad", ad_name)
    assert_version(doc, version)

    action = "manual_review_allow" if clean_decision == "approve" else "manual_review_reject"
    target = "Active" if clean_decision == "approve" else "Declined"
    transition = validate_status_transition(doc.status, target, action=action)
    apply_transition(doc, transition)
    doc.flags.aos_reviewed_by = reviewer
    doc.decline_reason = None if clean_decision == "approve" else clean_reason
    doc.save(ignore_permissions=True)

    owner = seller_user(doc.seller)
    if owner:
        if clean_decision == "approve":
            NotificationService.notify_ad_approved(user=owner, ad_id=doc.public_id, title=doc.title)
        else:
            NotificationService.notify_ad_rejected(user=owner, ad_id=doc.public_id, title=doc.title, reason=doc.decline_reason)
    enqueue_discovery_refresh(doc.name, status=doc.status, source=f"ad_{action}")
    return doc
