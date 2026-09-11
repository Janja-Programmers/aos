# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

from aos.services.notifications.service import NotificationService
from aos.services.sellers.policy import sync_verified_business_profile
from aos.services.verification.constants import (
    MAX_REJECTION_REASON_LENGTH,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_REVOKED,
    TYPE_BUSINESS,
    TYPE_INDIVIDUAL,
)
from aos.services.verification.errors import VerificationError
from aos.services.verification.lifecycle import (
    protect_review_metadata,
    stamp_review_metadata,
    validate_status_transition,
)
from aos.services.verification.observability import verification_log
from aos.services.verification.policy import lock_eligible_profile
from aos.services.verification.repository import lock_request_by_name
from aos.services.verification.validation import normalize_submit_payload


class AOSVerificationRequest(Document):
    def validate(self):
        previous = self._lock_authoritative_previous()
        self._validate_request(previous)
        try:
            protect_review_metadata(self, previous)
            validate_status_transition(self, previous, actor=frappe.session.user)
            stamp_review_metadata(self, previous, actor=frappe.session.user)
        except VerificationError as exc:
            frappe.throw(str(exc), frappe.ValidationError)
        self.flags.aos_verification_previous_status = previous.status if previous else None

    def on_update(self):
        self._sync_verification_result()

    def _lock_authoritative_previous(self):
        if self.is_new():
            return None

        # Approval follows the same account -> Verification lock order used by
        # submission/account deletion. The unlocked hint is advisory only; the
        # authoritative status is loaded after the Verification row is locked.
        status_hint = frappe.db.get_value("AOS Verification Request", self.name, "status")
        if self.status == STATUS_APPROVED and status_hint != STATUS_APPROVED:
            lock_eligible_profile(self.user)

        previous = lock_request_by_name(self.name)
        if not previous:
            frappe.throw(_("Verification request no longer exists."))
        if previous.user != self.user:
            frappe.throw(_("Verification request ownership cannot be changed."))
        return previous

    def _validate_request(self, previous=None):
        if not self.user:
            frappe.throw(_("User is required."))
        if not frappe.db.exists("User", self.user):
            frappe.throw(_("User does not exist."))
        if not frappe.db.exists("AOS Profile", {"user": self.user}):
            frappe.throw(_("AOS Profile does not exist for this user."))

        if previous and previous.user != self.user:
            frappe.throw(_("Verification request ownership cannot be changed."))
        if self.is_new() and frappe.db.exists("AOS Verification Request", {"user": self.user}):
            frappe.throw(_("A verification request already exists for this account."))

        try:
            normalized = normalize_submit_payload(self._submission_payload())
        except VerificationError as exc:
            frappe.throw(str(exc), frappe.ValidationError)
        self._apply_normalized_submission(normalized)

        self.rejection_reason = self._clean_text(
            self.rejection_reason,
            label="Rejection reason",
            max_length=MAX_REJECTION_REASON_LENGTH,
            multiline=True,
        ) or None
        if self.status == STATUS_REJECTED and not self.rejection_reason:
            frappe.throw(_("Rejection reason is required when request is rejected."))
        if self.status != STATUS_REJECTED:
            self.rejection_reason = None

        self._validate_documents()
        self._validate_submission_immutability(previous)

    def _submission_payload(self):
        payload = {
            "verification_type": self.verification_type,
            "verification_documents": [
                {
                    "document_type": row.document_type,
                    "document_number": row.document_number,
                    "issue_date": row.issue_date,
                    "expiry_date": row.expiry_date,
                    "media_id": row.media,
                }
                for row in (self.verification_documents or [])
            ],
        }
        if self.verification_type == TYPE_INDIVIDUAL:
            payload.update({"legal_name": self.legal_name, "phone_number": self.phone_number})
        elif self.verification_type == TYPE_BUSINESS:
            payload.update(
                {
                    "business_name": self.business_name,
                    "business_type": self.business_type,
                    "business_category": self.business_category,
                    "business_phone_number": self.business_phone_number,
                    "business_email": self.business_email,
                    "business_website": getattr(self, "business_website", None),
                    "business_address": self.business_address,
                }
            )
        return payload

    def _apply_normalized_submission(self, normalized):
        self.verification_type = normalized["verification_type"]
        normalized_documents = normalized["verification_documents"]
        for row, values in zip(self.verification_documents or [], normalized_documents):
            row.document_type = values["document_type"]
            row.document_number = values["document_number"] or None
            row.issue_date = values["issue_date"]
            row.expiry_date = values["expiry_date"]
            row.media = values["media_id"]
            row.attachment = ""

        if self.verification_type == TYPE_INDIVIDUAL:
            self.legal_name = normalized["legal_name"]
            self.phone_number = normalized["phone_number"]
            self.business_name = None
            self.business_type = None
            self.business_category = None
            self.business_phone_number = None
            self.business_email = None
            if hasattr(self, "business_website"):
                self.business_website = None
            self.business_address = None
            return

        self.business_name = normalized["business_name"]
        self.business_type = normalized["business_type"]
        self.business_category = normalized["business_category"]
        self.business_phone_number = normalized["business_phone_number"]
        self.business_email = normalized["business_email"]
        if hasattr(self, "business_website"):
            self.business_website = normalized.get("business_website") or None
        self.business_address = normalized["business_address"]
        self.legal_name = None
        self.phone_number = None
        if not frappe.db.exists("AOS Seller", {"user": self.user}):
            frappe.throw(_("AOS Seller is required for business verification."))

    def _validate_documents(self):
        seen: set[str] = set()
        for row in self.verification_documents or []:
            media_id = str(row.media or "").strip()
            if media_id in seen:
                frappe.throw(_("Duplicate verification document media is not allowed."))
            seen.add(media_id)
            media = frappe.db.get_value(
                "AOS Media Object",
                media_id,
                [
                    "purpose",
                    "visibility",
                    "status",
                    "owner_user",
                    "attached_doctype",
                    "attached_name",
                ],
                as_dict=True,
            )
            if not media or media.purpose != "verification_document" or media.visibility != "Private":
                frappe.throw(_("Invalid verification document media."))
            if media.owner_user != self.user:
                frappe.throw(_("Verification document media does not belong to this account."))
            if media.status not in {"Uploaded", "Attached"}:
                frappe.throw(_("Verification document media must be uploaded."))
            if media.status == "Attached" and (
                media.attached_doctype != "AOS Verification Request" or media.attached_name != self.name
            ):
                frappe.throw(_("Verification document media is already attached elsewhere."))

    def _validate_submission_immutability(self, previous):
        if not previous:
            return
        action = str(getattr(self.flags, "aos_verification_action", "") or "").strip()
        if action in {"resubmit", "system"}:
            return
        if self._submission_snapshot(previous) != self._submission_snapshot(self):
            frappe.throw(_("Submitted verification evidence cannot be edited during review."))
        old_hash = str(getattr(previous, "submission_idempotency_hash", "") or "")
        new_hash = str(getattr(self, "submission_idempotency_hash", "") or "")
        if old_hash != new_hash:
            frappe.throw(_("Verification submission idempotency metadata cannot be changed."))

    @staticmethod
    def _submission_snapshot(doc):
        return (
            str(doc.user or ""),
            str(doc.verification_type or ""),
            str(doc.legal_name or ""),
            str(doc.phone_number or ""),
            str(doc.business_name or ""),
            str(doc.business_type or ""),
            str(doc.business_category or ""),
            str(doc.business_phone_number or ""),
            str(doc.business_email or ""),
            str(getattr(doc, "business_website", "") or ""),
            str(doc.business_address or ""),
            tuple(
                (
                    str(row.document_type or ""),
                    str(row.document_number or ""),
                    str(row.issue_date or ""),
                    str(row.expiry_date or ""),
                    str(row.media or ""),
                )
                for row in (doc.verification_documents or [])
            ),
        )

    def _sync_verification_result(self):
        previous_status = getattr(self.flags, "aos_verification_previous_status", None)
        if previous_status == self.status:
            return

        if self.status == STATUS_APPROVED:
            self._approve_profile()
            self._sync_business_fields_if_needed()
            self._notify_approved()
            verification_log(
                "verification.approved",
                user=self.user,
                verification_id=self.name,
                status=self.status,
            )
        elif self.status == STATUS_REJECTED:
            self._notify_rejected()
            verification_log(
                "verification.rejected",
                user=self.user,
                verification_id=self.name,
                status=self.status,
            )
        elif self.status == STATUS_REVOKED:
            self._revoke_profile()
            verification_log(
                "verification.revoked",
                user=self.user,
                verification_id=self.name,
                status=self.status,
            )
        elif self.status == "Reviewing":
            verification_log(
                "verification.reviewing",
                user=self.user,
                verification_id=self.name,
                status=self.status,
            )

    def _approve_profile(self):
        name = frappe.db.get_value("AOS Profile", {"user": self.user}, "name")
        profile = frappe.get_doc("AOS Profile", name)
        profile.is_verified = 1
        profile.save(ignore_permissions=True)

    def _revoke_profile(self):
        other_approved = frappe.db.exists(
            "AOS Verification Request",
            {"user": self.user, "status": STATUS_APPROVED, "name": ["!=", self.name]},
        )
        if other_approved:
            return
        name = frappe.db.get_value("AOS Profile", {"user": self.user}, "name")
        profile = frappe.get_doc("AOS Profile", name)
        profile.is_verified = 0
        profile.save(ignore_permissions=True)

    def _sync_business_fields_if_needed(self):
        if self.verification_type != TYPE_BUSINESS:
            return
        sync_verified_business_profile(
            user=self.user,
            business_category=self.business_category,
            source="verification_approved",
        )

    def _notify_approved(self):
        try:
            NotificationService.notify_verification_approved(
                user=self.user,
                verification_id=self.name,
                decision_token=str(self.verified_on or ""),
            )
            verification_log(
                "verification.notification.enqueued",
                user=self.user,
                verification_id=self.name,
                status=STATUS_APPROVED,
            )
        except Exception:
            frappe.log_error(
                "verification_approved_notification_enqueue_failed",
                "AOS Verification Approved Notification Failed",
            )

    def _notify_rejected(self):
        try:
            NotificationService.notify_verification_rejected(
                user=self.user,
                verification_id=self.name,
                decision_token=str(self.verified_on or ""),
            )
            verification_log(
                "verification.notification.enqueued",
                user=self.user,
                verification_id=self.name,
                status=STATUS_REJECTED,
            )
        except Exception:
            frappe.log_error(
                "verification_rejected_notification_enqueue_failed",
                "AOS Verification Rejected Notification Failed",
            )

    @staticmethod
    def _clean_text(value, *, label: str, max_length: int, multiline: bool = False) -> str:
        text = str(value or "").replace("\x00", "").strip()
        if not multiline:
            text = " ".join(text.split())
        if len(text) > max_length:
            frappe.throw(_("{0} is too long.").format(label))
        return text

