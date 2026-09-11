"""Committed Verification decision projections and notification intents."""

from __future__ import annotations

import frappe

from aos.services.accounts.repository import AccountRepository
from aos.services.notifications.service import NotificationService

from .constants import STATUS_APPROVED, STATUS_REJECTED, STATUS_REVOKED
from .observability import verification_log


def apply_decision_side_effects(doc, *, previous_status: str | None) -> None:
    """Project an accepted state transition into finalized dependent domains.

    NotificationService persists its notification/delivery intent transactionally;
    its external delivery and realtime transports run after commit. Notification
    failures are isolated by that service and by this boundary.
    """
    if previous_status == doc.status:
        return

    if doc.status == STATUS_APPROVED:
        _set_account_verified(user=doc.user, value=True)
        _notify_approved(doc)
        _log_transition(doc, "verification.approved")
        return
    if doc.status == STATUS_REJECTED:
        _notify_rejected(doc)
        _log_transition(doc, "verification.rejected")
        return
    if doc.status == STATUS_REVOKED:
        _set_account_verified(user=doc.user, value=False)
        _log_transition(doc, "verification.revoked")
        return
    if doc.status == "Reviewing":
        _log_transition(doc, "verification.reviewing")


def _set_account_verified(*, user: str, value: bool) -> None:
    profile = AccountRepository.lock_profile(user)
    if not profile:
        frappe.throw("Account profile no longer exists.", frappe.ValidationError)
    desired = 1 if value else 0
    if int(getattr(profile, "is_verified", 0) or 0) == desired:
        return
    profile.is_verified = desired
    profile.save(ignore_permissions=True)


def _notify_approved(doc) -> None:
    try:
        NotificationService.notify_verification_approved(
            user=doc.user,
            verification_id=doc.name,
            decision_token=str(doc.verified_on or ""),
        )
        verification_log(
            "verification.notification.enqueued",
            user=doc.user,
            verification_id=doc.name,
            status=STATUS_APPROVED,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Verification approved notification intent failed",
        )


def _notify_rejected(doc) -> None:
    try:
        NotificationService.notify_verification_rejected(
            user=doc.user,
            verification_id=doc.name,
            decision_token=str(doc.verified_on or ""),
        )
        verification_log(
            "verification.notification.enqueued",
            user=doc.user,
            verification_id=doc.name,
            status=STATUS_REJECTED,
        )
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "AOS Verification rejected notification intent failed",
        )


def _log_transition(doc, event: str) -> None:
    verification_log(
        event,
        user=doc.user,
        verification_id=doc.name,
        status=doc.status,
    )
