# Copyright (c) 2026, Africa Online Stores and contributors
# For license information, please see license.txt

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document

from aos.services.media.media_service import MediaService
from aos.services.verification.constants import (
    MAX_REJECTION_REASON_LENGTH,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_REVOKED,
    TYPE_BUSINESS,
    TYPE_INDIVIDUAL,
)
from aos.services.verification.decision import apply_decision_side_effects
from aos.services.verification.evidence import validate_evidence_media
from aos.services.verification.errors import VerificationError
from aos.services.verification.lifecycle import (
    protect_review_metadata,
    stamp_review_metadata,
    validate_status_transition,
)
from aos.services.verification.policy import lock_eligible_profile, lock_profile
from aos.services.verification.repository import lock_request_by_name
from aos.services.verification.validation import normalize_submit_payload
from aos.utils.identifiers import new_prefixed_name

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class AOSVerificationRequest(Document):
    def autoname(self):
        self.name = new_prefixed_name("VER")

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
        apply_decision_side_effects(
            self,
            previous_status=getattr(self.flags, "aos_verification_previous_status", None),
        )

    def _lock_authoritative_previous(self):
        if self.is_new():
            return None

        # Decisions that mutate the Accounts verified projection follow the same
        # Accounts-profile -> Verification-row lock order as submission/deletion.
        if self.status == STATUS_APPROVED:
            lock_eligible_profile(self.user)
        elif self.status == STATUS_REVOKED:
            lock_profile(self.user)

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
            normalized = normalize_submit_payload(
                self._submission_payload(),
                require_idempotency=False,
            )
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
        self._validate_server_owned_metadata(previous)

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
                    "business_website": self.business_website,
                    "business_address": self.business_address,
                }
            )
        return payload

    def _apply_normalized_submission(self, normalized):
        self.verification_type = normalized["verification_type"]
        for row, values in zip(self.verification_documents or [], normalized["verification_documents"]):
            row.document_type = values["document_type"]
            row.document_number = values["document_number"] or None
            row.issue_date = values["issue_date"]
            row.expiry_date = values["expiry_date"]
            row.media = values["media_id"]

        if self.verification_type == TYPE_INDIVIDUAL:
            self.legal_name = normalized["legal_name"]
            self.phone_number = normalized["phone_number"]
            self.business_name = None
            self.business_type = None
            self.business_category = None
            self.business_phone_number = None
            self.business_email = None
            self.business_website = None
            self.business_address = None
            return

        self.business_name = normalized["business_name"]
        self.business_type = normalized["business_type"]
        self.business_category = normalized["business_category"]
        self.business_phone_number = normalized["business_phone_number"]
        self.business_email = normalized["business_email"]
        self.business_website = normalized.get("business_website") or None
        self.business_address = normalized["business_address"]
        self.legal_name = None
        self.phone_number = None

    def _validate_documents(self):
        media = MediaService()
        for row in self.verification_documents or []:
            try:
                validate_evidence_media(
                    media=media,
                    media_id=str(row.media or "").strip(),
                    user=self.user,
                    verification_name=self.name,
                )
            except VerificationError as exc:
                frappe.throw(str(exc), frappe.ValidationError)

    def _validate_submission_immutability(self, previous):
        if not previous:
            return
        action = str(getattr(self.flags, "aos_verification_action", "") or "").strip()
        if action in {"resubmit", "system"}:
            return
        if self._submission_snapshot(previous) != self._submission_snapshot(self):
            frappe.throw(_("Submitted verification evidence cannot be edited during review."))

    def _validate_server_owned_metadata(self, previous):
        action = str(getattr(self.flags, "aos_verification_action", "") or "").strip()
        key_hash = str(self.submission_idempotency_key_hash or "").strip()
        payload_hash = str(self.submission_payload_hash or "").strip()
        if not self.submitted_on or not _SHA256_RE.fullmatch(key_hash) or not _SHA256_RE.fullmatch(payload_hash):
            frappe.throw(_("Verification submission metadata is invalid."))

        if previous is None:
            if action != "submit":
                frappe.throw(_("Verification requests must be created through the submission service."))
            return
        if action in {"resubmit", "system"}:
            return
        if str(self.submitted_on or "") != str(previous.submitted_on or ""):
            frappe.throw(_("Verification submission time cannot be edited."))
        if key_hash != str(previous.submission_idempotency_key_hash or ""):
            frappe.throw(_("Verification idempotency metadata cannot be edited."))
        if payload_hash != str(previous.submission_payload_hash or ""):
            frappe.throw(_("Verification payload metadata cannot be edited."))

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
            str(doc.business_website or ""),
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

    @staticmethod
    def _clean_text(value, *, label: str, max_length: int, multiline: bool = False) -> str:
        text = str(value or "").replace("\x00", "").strip()
        if not multiline:
            text = " ".join(text.split())
        if len(text) > max_length:
            frappe.throw(_("{0} is too long.").format(label))
        return text
